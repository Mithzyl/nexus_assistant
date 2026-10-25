from playwright.sync_api import sync_playwright
import json, os
BASE=os.environ.get('SMOKE_BASE_URL', 'http://localhost:3200'); out=os.environ.get('SMOKE_SHOT_DIR', os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'shots'))
errs=[]; results=[]
def check(n,c,extra=''): results.append((n,bool(c),extra)); print(('PASS' if c else 'FAIL'), n, extra[:240], flush=True)
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={'width':390,'height':844})
    page=ctx.new_page(); page.on('pageerror', lambda e: errs.append(str(e)))
    # wrong password
    page.goto(BASE+'/login', wait_until='networkidle')
    page.fill('#login-email','web4@example.com'); page.fill('#login-password','wrong-password')
    page.locator('form').get_by_role('button', name='登录').click(); page.wait_for_timeout(1200)
    body=page.locator('body').inner_text()
    check('wrong password message', '不正确' in body and '/login' in page.url, body[:300])
    # login
    page.fill('#login-password','pa-web-verify-2026')
    page.locator('form').get_by_role('button', name='登录').click(); page.wait_for_url('**/today'); page.wait_for_timeout(800)
    cookies={c['name']:c for c in ctx.cookies()}
    print('COOKIES', json.dumps({k:{kk:vv for kk,vv in v.items() if kk in ('httpOnly','secure','sameSite','path','expires')} for k,v in cookies.items()}, default=str))
    check('session cookie httpOnly', cookies.get('pa_session',{}).get('httpOnly') is True)
    # cross-site write
    r=ctx.request.post(BASE+'/api/auth/session/renew', headers={'Origin':'https://evil.example'}, data={})
    check('cross-site renew blocked', r.status==403, str(r.status))
    r2=ctx.request.post(BASE+'/api/auth/session/renew', headers={'Origin':BASE}, data={})
    check('same-site renew ok', r2.status==200, str(r2.status))
    # sources
    page.goto(BASE+'/me/sources', wait_until='networkidle'); page.wait_for_timeout(700)
    body=page.locator('body').inner_text()
    check('sources page renders', '内容来源' in body, body[:300])
    page.screenshot(path=out+'/smoke-sources.png', full_page=True)
    # memory search (legacy)
    page.goto(BASE+'/me/memory-search', wait_until='networkidle'); page.wait_for_timeout(900)
    body=page.locator('body').inner_text()
    check('memory search reaches page', '记忆' in body, body[:300])
    page.screenshot(path=out+'/smoke-memory-search.png', full_page=True)
    # enable demo on me
    page.goto(BASE+'/me', wait_until='networkidle'); page.wait_for_timeout(600)
    sw=page.get_by_role('switch')
    print('SWITCH COUNT', sw.count())
    if sw.count():
        sw.first.click(); page.wait_for_timeout(600)
    page.goto(BASE+'/matters', wait_until='networkidle'); page.wait_for_timeout(600)
    body=page.locator('body').inner_text()
    check('demo matters shown', '演示' in body and '退款' in body, body[:400])
    page.screenshot(path=out+'/smoke-matters-demo.png', full_page=True)
    page.goto(BASE+'/matters/coffee', wait_until='networkidle'); page.wait_for_timeout(600)
    body=page.locator('body').inner_text()
    check('demo matter detail coffee', len(body)>50, body[:300])
    page.goto(BASE+'/me/memories', wait_until='networkidle'); page.wait_for_timeout(600)
    body=page.locator('body').inner_text()
    check('memories demo page', '记忆' in body, body[:300])
    page.screenshot(path=out+'/smoke-memories.png', full_page=True)
    # 404
    r=page.goto(BASE+'/no-such-page', wait_until='networkidle')
    check('404 status', r.status==404, str(r.status))
    print('PAGEERRORS', json.dumps(errs, ensure_ascii=False))
    print('FAILED', [x[0] for x in results if not x[1]])
    b.close()
