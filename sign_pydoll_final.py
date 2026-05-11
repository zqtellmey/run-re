import asyncio, os, re, time, logging, random, base64, traceback, json, math
from pathlib import Path
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)
COOKIE_FILE = Path("cookies.json")   # 新增

# ---------- WxPusher 推送 ----------
WXPUSHER_TOKEN = os.environ.get("WXPUSHER_TOKEN", "")
WXPUSHER_UID   = os.environ.get("WXPUSHER_UID", "")

def wxpush(content: str):
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        log.warning("📨 WXPUSHER_TOKEN 或 WXPUSHER_UID 未配置，跳过推送")
        return
    import urllib.request
    payload = json.dumps({
        "appToken": WXPUSHER_TOKEN,
        "content": content,
        "contentType": 1,
        "uids": [WXPUSHER_UID],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                log.info("📨 WxPusher 推送成功")
            else:
                log.warning(f"📨 WxPusher 推送失败: {result}")
    except Exception as e:
        log.warning(f"📨 WxPusher 推送异常: {e}")

ocr = ddddocr.DdddOcr(beta=True, show_ad=False)

# ---------- Cookie 管理（新增）----------
def load_cookies():
    if COOKIE_FILE.exists():
        try:
            with open(COOKIE_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_cookies(cookies):
    with open(COOKIE_FILE, 'w') as f:
        json.dump(cookies, f)

# ---------- CDP 截图 ----------
async def take_screenshot(browser, tab, name):
    try:
        conn = getattr(browser, '_connection', None) or getattr(browser, 'connection', None)
        if not conn:
            return
        result = await conn.execute("Page.captureScreenshot", {"format": "png"})
        data = result.get("data", "")
        if data:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"{ts}_{name}.png"
            Path(path).write_bytes(base64.b64decode(data))
            log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

async def get_text(tab):
    try:
        result = await tab.execute_script("return document.body.innerText")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def get_url(tab):
    try:
        raw = await tab.execute_script("return window.location.href")
        if isinstance(raw, dict):
            return raw.get("result", {}).get("result", {}).get("value", "") or str(raw)
        return str(raw)
    except:
        return ""

async def human_delay(min_s=0.3, max_s=0.8):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------- 浏览器 ----------
def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    import subprocess
    try:
        result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True, timeout=5)
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except:
        pass
    return None

async def create_browser():
    opts = ChromiumOptions()
    opts.headless = False
    path = _find_chromium()
    if path:
        opts.binary_location = path
    opts.add_argument("--window-size=1280,720")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-save-password-bubble")
    opts.add_argument("--disable-password-generation")
    opts.add_argument("--password-store=basic")
    opts.add_argument("--use-mock-keychain")
    opts.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    opts.browser_preferences = {
        "credentials_enable_service": False,
        "profile": {"password_manager_enabled": False},
    }
    browser = await Chrome(options=opts).__aenter__()
    tab = await browser.start()
    return browser, tab

# ---------- Cloudflare 手动点击 ----------
async def manual_cf_click(tab, timeout=15):
    log.info("尝试手动完成 Cloudflare 验证...")
    for _ in range(timeout):
        body = await get_text(tab)
        if "email" in body or "登录" in body:
            return True
        await tab.execute_script("""
            (function() {
                const checkboxes = document.querySelectorAll('iframe');
                for (let iframe of checkboxes) {
                    try {
                        const doc = iframe.contentDocument || iframe.contentWindow.document;
                        const cb = doc.querySelector('#checkbox, input[type="checkbox"]');
                        if (cb) {
                            cb.click();
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    } catch(e) {}
                }
            })()
        """)
        await asyncio.sleep(1)
    return False

# ---------- 验证码 ----------
async def fill_captcha(tab):
    for _ in range(3):
        try:
            cap_img = await tab.find(id="allow_login_email_captcha", timeout=5)
        except:
            try:
                cap_img = await tab.find(tag_name="img", alt="验证码", timeout=5)
            except:
                cap_img = None
        if cap_img:
            src = cap_img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                raw = ocr.classification(base64.b64decode(b64))
                code = re.sub(r'[^0-9]', '', raw)
                log.info(f"验证码: {raw} -> {code}")
                await tab.execute_script(f"""
                    (function() {{
                        var input = document.querySelector('input[name="captcha"]') ||
                                    document.querySelector('input[id*="captcha"]') ||
                                    document.querySelector('input[placeholder*="验证码"]');
                        if (input) {{
                            input.focus();
                            input.value = '{code}';
                            input.dispatchEvent(new Event('input', {{bubbles:true}}));
                            input.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }}
                    }})()
                """)
                return code
        await asyncio.sleep(1)
    return ""

# ---------- 登录（增加 Cookie 恢复）----------
async def login(browser, tab, max_retries=3):
    # 先尝试用缓存 Cookie 恢复登录
    cookies = load_cookies()
    if cookies:
        log.info("尝试用缓存的 Cookie 恢复登录...")
        await tab.go_to(BASE_URL)
        await asyncio.sleep(1)
        for c in cookies:
            try:
                script = f"""
                    (function() {{
                        var cookie = {json.dumps(c['name'])} + "=" + {json.dumps(c['value'])};
                        if ({json.dumps(c.get('domain', ''))}) cookie += ";domain=" + {json.dumps(c.get('domain', ''))};
                        if ({json.dumps(c.get('path', ''))}) cookie += ";path=" + {json.dumps(c.get('path', ''))};
                        if ({json.dumps(c.get('secure', False))}) cookie += ";secure";
                        document.cookie = cookie;
                    }})()
                """
                await tab.execute_script(script)
            except:
                pass
        await tab.go_to(USER_CENTER)
        await asyncio.sleep(3)
        url = await get_url(tab)
        if "/clientarea" in url and "login" not in url:
            log.info("✅ Cookie 有效，已恢复登录")
            return True
        else:
            log.info("Cookie 失效，需要重新登录")

    # 否则进行全新登录
    for attempt in range(1, max_retries + 1):
        log.info(f"登录尝试 {attempt}/{max_retries}")
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to(LOGIN_URL)
        except:
            await tab.go_to(LOGIN_URL)

        await asyncio.sleep(3)
        body = await get_text(tab)
        if "verify you are human" in body.lower():
            if not await manual_cf_click(tab):
                log.warning("Cloudflare 验证可能未完成")

        # 填邮箱
        email_el = None
        for sel in [{"tag_name": "input", "name": "email"}, {"placeholder": "请输入邮箱地址"}, {"placeholder": "请输入您的邮箱"}]:
            try:
                email_el = await tab.find(**sel, timeout=5)
                break
            except:
                continue
        if not email_el:
            log.warning("未找到邮箱输入框")
            continue
        await email_el.click()
        await email_el.type_text("")
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()

        # 填密码
        pass_el = None
        for sel in [{"tag_name": "input", "name": "password"}, {"placeholder": "请输入登录密码"}, {"placeholder": "请输入您的密码"}]:
            try:
                pass_el = await tab.find(**sel, timeout=5)
                break
            except:
                continue
        if not pass_el:
            log.warning("未找到密码输入框")
            continue
        await pass_el.click()
        await pass_el.type_text("")
        await pass_el.type_text(PASSWORD, humanize=True)

        captcha = await fill_captcha(tab)
        if not captcha:
            log.warning("没有验证码")
            continue

        # 登录按钮
        try:
            login_btn = await tab.find(css="button.btn.btn-primary", timeout=10)
        except:
            login_btn = await tab.find(tag_name="button", text="登录", timeout=10)
        await login_btn.click()
        await asyncio.sleep(5)

        url = await get_url(tab)
        if "/clientarea" in url:
            log.info("✅ 登录成功")
            # 保存 Cookie
            try:
                conn = getattr(browser, '_connection', None) or getattr(browser, 'connection', None)
                result = await conn.execute("Network.getCookies", {"urls": [BASE_URL, LOGIN_URL, USER_CENTER]})
                cookie_list = [{
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", ""),
                    "secure": c.get("secure", False),
                } for c in result.get("cookies", [])]
                save_cookies(cookie_list)
                log.info(f"已保存 {len(cookie_list)} 个 Cookie")
            except Exception as e:
                log.warning(f"保存 Cookie 失败: {e}")
            return True

        log.warning(f"登录失败，URL: {url}")
        await asyncio.sleep(1)

    log.error("多次登录尝试均失败")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("前往签到页...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(SIGN_PAGE)
    except:
        await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(3)

    try:
        btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
        await btn.click()
    except:
        log.info("可能已经签到过了")
        await take_screenshot(browser, tab, "02_sign_skip")
        return None

    await asyncio.sleep(2)
    text = await get_text(tab)
    match = re.search(r'请计算[：:]\s*(\d+)\s*([+\-*/])\s*(\d+)', text)
    if match:
        a, op, b = int(match[1]), match[2], int(match[3])
        if op == '+': result = a + b
        elif op == '-': result = a - b
        elif op == '*': result = a * b
        elif op == '/': result = a / b if b != 0 else 0
        else: result = 0
        result_str = str(int(result)) if result == int(result) else f"{math.floor(result * 100 + 0.5) / 100:.2f}".rstrip("0").rstrip(".")
        ans_el = await tab.find(placeholder="请输入答案", timeout=5)
        await ans_el.click()
        await ans_el.type_text(result_str, humanize=True)
        ver_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
        await ver_btn.click()
        await asyncio.sleep(2)

    for _ in range(2):
        try:
            ok = await tab.find(tag_name="button", text="确定", timeout=3)
            await ok.click()
            await asyncio.sleep(1)
        except:
            break
    log.info("签到完成")
    await take_screenshot(browser, tab, "03_sign_complete")

    text_after = await get_text(tab)
    bal = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text_after)
    if bal:
        return bal.group(1)
    bal2 = re.search(r'(?:积分余额|账户余额)[：:\s]+([\d.]+)\s*积分', text_after)
    return bal2.group(1) if bal2 else None

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(USER_CENTER)
    except:
        await tab.go_to(USER_CENTER)
    await asyncio.sleep(3)

    text = await get_text(tab)
    clientarea_balance = None
    bal = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text)
    if bal:
        clientarea_balance = bal.group(1)
    else:
        bal2 = re.search(r'([\d.]+)\s*\n?\s*积分\s*可用余额|可用余额\s*([\d.]+)\s*\n?\s*积分', text)
        if bal2:
            clientarea_balance = bal2.group(1) or bal2.group(2)

    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日，跳过续费")
        return None, None, False, clientarea_balance
    expiry_str = match.group(1)
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {expiry_str}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return expiry_str, remain, False, clientarea_balance

    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=10)
        await renew_btn.click()
    except:
        return expiry_str, remain, False, clientarea_balance
    await asyncio.sleep(2)
    try:
        confirm = await tab.find(tag_name="button", text="立即续费", timeout=5)
        await confirm.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        pay = await tab.find(tag_name="button", text="立即支付", timeout=5)
        await pay.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
    except:
        pass
    log.info("续费完成")
    await take_screenshot(browser, tab, "04_renew_complete")
    return expiry_str, remain, True, clientarea_balance

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            wxpush("❌ Runfreecloud 登录失败，请检查账号密码或验证码")
            return

        balance = await sign(browser, tab)
        expiry_str, remain, renewed, clientarea_balance = await renew(browser, tab)
        final_balance = balance if balance is not None else clientarea_balance
        lines = ["✅ 签到成功"]
        if final_balance is not None:
            lines.append(f"账户余额剩余 {final_balance} 积分")
        if expiry_str:
            lines.append(f"到期时间 {expiry_str}")
            if renewed:
                lines.append("已自动续期")
        wxpush("\n".join(lines))
    except Exception as e:
        log.error(f"任务失败: {e}")
        traceback.print_exc()
        await take_screenshot(browser, tab, "99_error")
        wxpush(f"❌ Runfreecloud 任务异常: {e}")
    finally:
        await asyncio.sleep(5)
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
