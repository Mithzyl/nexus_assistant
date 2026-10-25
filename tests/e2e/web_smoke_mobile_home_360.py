from playwright.sync_api import sync_playwright
import json, os
BASE=os.environ.get('SMOKE_BASE_URL', 'http://localhost:3200'); out=os.environ.get('SMOKE_SHOT_DIR', os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'shots'))
errs=[]; results=[]
def check(n,c,extra=''): results.append((n,bool(c),extra)); print(('PASS' if c else 'FAIL'), n, extra[:160], flush=True)
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={'width':360,'height':844})
    page=ctx.new_page(); page.on('pageerror', lambda e: errs.append(str(e)))
    page.goto(BASE+'/login', wait_until='networkidle')
    page.fill('#login-email','web4@example.com'); page.fill('#login-password','pa-web-verify-2026')
    page.locator('form').get_by_role('button', name='登录').click(); page.wait_for_url('**/today'); page.wait_for_timeout(900)
    for path in ['/today','/matters','/ask','/me','/me/commute','/me/commute/2b32ace9-2267-43ae-ac61-e221b3da5838','/me/sources']:
        page.goto(BASE+path, wait_until='networkidle'); page.wait_for_timeout(500)
        m=page.evaluate("() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
        check(f'no h-overflow 360 {path}', m['sw']<=m['cw']+1, json.dumps(m))
    page.goto(BASE+'/today', wait_until='networkidle'); page.wait_for_timeout(600)
    page.screenshot(path=out+'/smoke-today-360.png', full_page=True)
    # dark mode toggle on me
    page.goto(BASE+'/me', wait_until='networkidle'); page.wait_for_timeout(600)
    before=page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    # find a control mentioning 深色
    dark=page.get_by_role('button', name='深色')
    print('DARK BUTTONS', dark.count())
    if dark.count():
        dark.first.click(); page.wait_for_timeout(500)
    after=page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    check('dark mode changes surface', before!=after, f'{before} -> {after}')
    page.screenshot(path=out+'/smoke-me-dark.png', full_page=True)
    print('PAGEERRORS', json.dumps(errs, ensure_ascii=False))
    print('FAILED', [x[0] for x in results if not x[1]])
    b.close()
