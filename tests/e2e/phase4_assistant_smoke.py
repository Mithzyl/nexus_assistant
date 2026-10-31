from playwright.sync_api import sync_playwright
import os, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
SHOTS = pathlib.Path(os.environ.get('SMOKE_SHOT_DIR', ROOT / '.tmp' / 'shots'))
DB = os.environ.get('SMOKE_DB', str(ROOT / '.tmp' / 'dev' / 'assistant.db'))
import subprocess, json, sqlite3, time
BASE='http://127.0.0.1:3100'; ENV={**os.environ,'ASSISTANT_ENVIRONMENT':'development','ASSISTANT_DATABASE_URL':f'sqlite+aiosqlite:///{DB}','ASSISTANT_ALLOW_SCHEMA_CREATE':'true','ASSISTANT_NEXUS_BASE_URL':'http://127.0.0.1:8001','ASSISTANT_NEXUS_APPLICATION_TOKEN':'stub-application-token-32-characters','ASSISTANT_TOOL_CALLBACK_TOKEN':'tool-callback-token-32-characters','ASSISTANT_COMMUTE_PROVIDER_MODE':'mock'}
res=[]; errs=[]
def ok(n,c,e=''): res.append((n,bool(c),e)); print(('PASS' if c else 'FAIL'),n,e,flush=True)
def db(q,args=()):
    c=sqlite3.connect(DB); r=list(c.execute(q,args)); c.close(); return r
def worker():
    subprocess.run([str(ROOT / '.tmp' / 'devvenv' / 'bin' / 'python'),'-m','assistant_api.commute.worker','--once'], cwd='services/assistant-api', env=ENV, capture_output=True, timeout=120)
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2); page=ctx.new_page()
    page.on('pageerror', lambda e: errs.append(str(e)))
    page.goto(BASE+'/login', wait_until='networkidle'); page.wait_for_timeout(700)
    page.fill('#login-email','web4@example.com'); page.fill('#login-password','pa-web-verify-2026')
    page.locator('form').get_by_role('button', name='登录').click(); page.wait_for_url('**/today'); page.wait_for_timeout(600)
    page.goto(BASE+'/me/commute/new', wait_until='networkidle'); page.wait_for_timeout(500)
    page.fill('#commute-name','第四轮通勤验收')
    page.fill('#commute-arrival','09:00'); page.fill('#commute-check','07:30')
    page.fill('#origin-label','验收起点'); page.fill('#origin-lat','31.2304'); page.fill('#origin-lng','121.4737')
    page.fill('#destination-label','验收终点'); page.fill('#destination-lat','31.2019'); page.fill('#destination-lng','121.606')
    page.get_by_role('button', name='保存并继续').click(); page.wait_for_timeout(1200)
    plan=page.url.rstrip('/').split('/')[-1]
    page.get_by_role('button', name='重新分析').first.click(); page.wait_for_timeout(1800)
    body=page.locator('body').inner_text()
    ok('queued shown as waiting', '等待处理' in body and '处理中' not in body.split('本次操作状态')[-1][:20], body[:400])
    ok('no fake execution stage', '查询天气' not in body and '调用 AI' not in body, body[:400])
    rows=db('select check_id,status from commute_checks where plan_id=?',(plan,))
    ok('one queued check', len(rows)==1 and rows[0][1]=='queued', str(rows))
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1800)
    ok('reload restores same check', db('select count(*) from commute_checks where plan_id=?',(plan,))[0][0]==1)
    ok('restored waiting status', '等待处理' in page.locator('body').inner_text())
    page.screenshot(path=str(SHOTS)+'/phase4-waiting.png', full_page=True)
    worker(); page.reload(wait_until='networkidle'); page.wait_for_timeout(2500)
    body=page.locator('body').inner_text()
    ok('completed result shown', '已完成' in body and '在有效期内' in body, body[:500])
    ok('server route + mock labelled', '实时路线查询' in body and '开发/测试数据' in body, body[:500])
    ok('plan requirements prioritized', '计划要求' in body and '到达' in body and '检查' in body)
    page.screenshot(path=str(SHOTS)+'/phase4-current.png', full_page=True)
    # plan revision change -> old result not current
    page.get_by_role('button', name='设置').click(); page.wait_for_timeout(400)
    page.fill('#commute-name','第四轮通勤验收 v2')
    page.get_by_role('button', name='保存计划').click(); page.wait_for_timeout(1600)
    page.get_by_role('button', name='建议').click(); page.wait_for_timeout(1200)
    body=page.locator('body').inner_text()
    ok('old revision archived', '旧计划版本' in body and '历史建议' in body, body[:500])
    ok('old result not current after revision', '在有效期内' not in body, body[:500])
    page.screenshot(path=str(SHOTS)+'/phase4-revision-history.png', full_page=True)
    # expire latest and check history remains
    adv=db("select advisory_id from commute_advisories where plan_id=? order by generated_at desc limit 1",(plan,))[0][0]
    c=sqlite3.connect(DB); c.execute("update commute_advisories set expires_at='2026-10-24 10:00:00' where advisory_id=?",(adv,)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1800)
    body=page.locator('body').inner_text()
    ok('expired archive', '已过期' in body and '历史建议' in body and '当前没有未过期建议' in body, body[:600])
    page.screenshot(path=str(SHOTS)+'/phase4-expired.png', full_page=True)
    # route unconfigured copy
    c=sqlite3.connect(DB); old=c.execute('select ai_analysis_json from commute_advisories where advisory_id=?',(adv,)).fetchone()[0]; ai=json.loads(old) if old else {}
    ai.update({'status':'unavailable','reason':'route_provider_unconfigured','route_source':'none'})
    c.execute('update commute_advisories set ai_analysis_json=?, expires_at=? where advisory_id=?',(json.dumps(ai,ensure_ascii=False),'2026-10-25 15:00:00',adv)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1800)
    body=page.locator('body').inner_text()
    ok('unconfigured two-part copy', '当前无法查询新路线' in body and '此前客户端快照产生的历史建议' in body, body[:700])
    page.screenshot(path=str(SHOTS)+'/phase4-unconfigured.png', full_page=True)
    # old field compatibility: remove base/additional, keep total extra
    c=sqlite3.connect(DB); old=c.execute('select ai_analysis_json from commute_advisories where advisory_id=?',(adv,)).fetchone()[0]; ai=json.loads(old) if old else {}
    ai.update({'status':'available','route_source':'client_snapshot','base_buffer_seconds':None,'additional_buffer_seconds':None,'extra_buffer_seconds':1200})
    c.execute('update commute_advisories set ai_analysis_json=?, explanation_source=?, expires_at=? where advisory_id=?',(json.dumps(ai,ensure_ascii=False),'model','2026-10-25 15:00:00',adv)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1500)
    if page.locator('details.history-advice').count():
        page.locator('details.history-advice').first.locator('summary').click(); page.wait_for_timeout(300)
    body=page.locator('body').inner_text()
    ok('old fields no AI zero', 'AI 额外 0' not in body and '0 分钟' not in body.split('历史总预留')[-1][:20] if '历史总预留' in body else True, body[-700:])
    ok('old total labelled historical', '历史总预留' in body and '未拆分 AI 额外' in body, body[-700:])
    print('PAGE ERRORS', json.dumps(errs, ensure_ascii=False))
    print('FAILED', [r[0] for r in res if not r[1]])
    b.close()
