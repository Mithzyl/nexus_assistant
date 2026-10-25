from playwright.sync_api import sync_playwright
import os, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
SHOTS = pathlib.Path(os.environ.get('SMOKE_SHOT_DIR', ROOT / '.tmp' / 'shots'))
DB = os.environ.get('SMOKE_DB', str(ROOT / '.tmp' / 'dev' / 'assistant.db'))
import sqlite3, json
BASE='http://127.0.0.1:3100'; PLAN='2b32ace9-2267-43ae-ac61-e221b3da5838'
res=[]
def ok(n,c,e=''): res.append((n,bool(c),e)); print(('PASS' if c else 'FAIL'),n,e,flush=True)
def db(q,args=()):
    c=sqlite3.connect(DB); r=list(c.execute(q,args)); c.close(); return r
with sync_playwright() as p:
    b=p.chromium.launch(); page=b.new_page(viewport={"width":390,"height":844}, device_scale_factor=2)
    page.goto(BASE+'/login', wait_until='networkidle'); page.wait_for_timeout(600)
    page.fill('#login-email','web4@example.com'); page.fill('#login-password','pa-web-verify-2026')
    page.locator('form').get_by_role('button', name='登录').click(); page.wait_for_url('**/today'); page.wait_for_timeout(700)
    page.goto(BASE+'/me/commute/'+PLAN, wait_until='networkidle'); page.wait_for_timeout(1800)
    body=page.locator('body').inner_text()
    ok('current advice valid', '在有效期内' in body and '已完成' in body, body[:500])
    # plan revision change
    page.get_by_role('tab', name='设置').click(); page.wait_for_timeout(500)
    print('settings name field count', page.locator('#commute-name').count())
    page.fill('#commute-name','第四轮通勤验收 v2')
    page.get_by_role('button', name='保存计划').click(); page.wait_for_timeout(1800)
    page.get_by_role('tab', name='建议').click(); page.wait_for_timeout(1200)
    body=page.locator('body').inner_text()
    ok('old revision archived', '旧计划版本' in body and '历史建议' in body, body[:700])
    ok('old result not current after revision', '在有效期内' not in body, body[:500])
    page.screenshot(path=str(SHOTS)+'/phase4-revision-history.png', full_page=True)
    # expire old advisory
    adv=db('select advisory_id from commute_advisories where plan_id=? order by generated_at desc limit 1',(PLAN,))[0][0]
    c=sqlite3.connect(DB); c.execute("update commute_advisories set expires_at='2026-10-24 10:00:00' where advisory_id=?",(adv,)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1500)
    body=page.locator('body').inner_text()
    ok('expired archive', '已过期' in body and '历史建议' in body and '当前没有未过期建议' in body, body[:700])
    page.screenshot(path=str(SHOTS)+'/phase4-expired.png', full_page=True)
    # route unconfigured
    c=sqlite3.connect(DB); old=c.execute('select ai_analysis_json from commute_advisories where advisory_id=?',(adv,)).fetchone()[0]; ai=json.loads(old) if old else {}
    ai.update({'status':'unavailable','reason':'route_provider_unconfigured','route_source':'none'})
    c.execute('update commute_advisories set ai_analysis_json=?, expires_at=? where advisory_id=?',(json.dumps(ai,ensure_ascii=False),'2026-10-25 15:00:00',adv)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1500)
    body=page.locator('body').inner_text()
    ok('unconfigured two-part copy', '当前无法查询新路线' in body and '此前客户端快照产生的历史建议' in body, body[:800])
    page.screenshot(path=str(SHOTS)+'/phase4-unconfigured.png', full_page=True)
    # old field compatibility
    c=sqlite3.connect(DB); old=c.execute('select ai_analysis_json from commute_advisories where advisory_id=?',(adv,)).fetchone()[0]; ai=json.loads(old) if old else {}
    ai.update({'status':'available','route_source':'client_snapshot','base_buffer_seconds':None,'additional_buffer_seconds':None,'extra_buffer_seconds':1200})
    c.execute('update commute_advisories set ai_analysis_json=?, explanation_source=?, expires_at=? where advisory_id=?',(json.dumps(ai,ensure_ascii=False),'model','2026-10-25 15:00:00',adv)); c.commit(); c.close()
    page.reload(wait_until='networkidle'); page.wait_for_timeout(1500)
    if page.locator('details.history-advice').count():
        page.locator('details.history-advice').first.locator('summary').click(); page.wait_for_timeout(300)
    body=page.locator('body').inner_text()
    ok('old total labelled historical', '历史总预留' in body and '未拆分 AI 额外' in body, body[-800:])
    ok('no fabricated AI zero', 'AI 额外 0' not in body and 'AI 额外 0 分钟' not in body, body[-500:])
    page.screenshot(path=str(SHOTS)+'/phase4-old-fields.png', full_page=True)
    print('FAILED', [r[0] for r in res if not r[1]])
    b.close()
