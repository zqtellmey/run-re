import asyncio, os, re, time, logging, random, base64, traceback, json, math
from pathlib import Path
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------- 全局配置 ----------
EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

COOKIE_FILE = Path("./cookies.json")

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

# ---------- 工具函数 ----------
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

async def take_screenshot(browser, tab, name):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        await tab.take_screenshot(path=path)
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
        result = await tab.execute_script("return window.location.href")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def human_delay(min_s=0.3, max_s=0.8):
    await asyncio.sleep(random.uniform(min_s, max_s))

async def wait_for_url_contains(tab, keyword, timeout=10):
    """轮询等待 URL 包含指定关键字"""
    for _ in range(timeout):
        url = await get_url(tab)
        if keyword in url:
            return True
        await asyncio.sleep(0.5)
    return False

async def wait_for_element_by_text(tab, text, timeout=10):
    """轮询等待页面出现指定文字"""
    for _ in range(timeout * 2):
        body = await get_text(tab)
        if text in body:
            return True
        await asyncio.sleep(0.5)
    return False

# ---------- 浏览器管理 ----------
def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            log.info(f"找到 Chromium: {p}")
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

# ---------- Cloudflare 交互 ----------
async def manual_cf_click(tab, timeout=15):
    log.info("尝试手动完成 Cloudflare 验证...")
    for i in range(timeout):
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
                            cb.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    } catch(e) {}
                }
            })()
        """)
        await asyncio.sleep(1)
    return False

# ---------- 验证码处理 ----------
async def fill_captcha(tab):
    for _ in range(3):
        try:
            cap_img = await tab.find(id="allow_login_email_captcha", timeout=5)
        except:
            cap_img = None
        if not cap_img:
            try:
                cap_img = await tab.find(tag_name="img", alt="验证码", timeout=5)
            except:
                cap_img = None
        if cap_img:
            src = cap_img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                raw = ocr.classification(img_bytes)
                code = re.sub(r'[^0-9]', '', raw)
                log.info(f"识别验证码: {code}")
                await tab.execute_script(f"""
                    (function() {{
                        var input = document.querySelector('#captcha_allow_login_email_captcha') ||
                                    document.querySelector('input[name="captcha"]') ||
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

# ---------- Cookie 管理 ----------
async def try_restore_cookies(tab):
    cookies = load_cookies()
    if not cookies:
        return False
    log.info("尝试 Cookie 恢复登录...")
    await tab.go_to(BASE_URL)
    await asyncio.sleep(1)
    for c in cookies:
        try:
            await tab.set_cookie(name=c["name"], value=c["value"], domain="run.freecloud.ltd", path=c.get("path", "/"))
        except:
            pass
    await tab.go_to(USER_CENTER)
    if await wait_for_url_contains(tab, "/clientarea", 6):
        log.info("✅ Cookie 有效，已恢复登录")
        return True
    log.info("Cookie 已失效")
    return False

async def save_session_cookies(tab):
    try:
        cookies = await tab.get_cookies()
        if cookies:
            cookie_list = [{
                "name": c.get("name", ""), "value": c.get("value", ""),
                "domain": c.get("domain", ""), "path": c.get("path", "/"),
                "secure": c.get("secure", False),
            } for c in cookies if c.get("name") and c.get("value")]
            save_cookies(cookie_list)
            log.info(f"✅ 已保存 {len(cookie_list)} 个 Cookie")
    except Exception as e:
        log.warning(f"保存 Cookie 失败: {e}")

# ---------- 登录 ----------
async def login(browser, tab, max_retries=3):
    if await try_restore_cookies(tab):
        return True

    for attempt in range(1, max_retries + 1):
        log.info(f"登录 {attempt}/{max_retries}")
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to(LOGIN_URL)
        except:
            await tab.go_to(LOGIN_URL)

        await asyncio.sleep(2)
        body = await get_text(tab)
        if "verify you are human" in body.lower():
            await manual_cf_click(tab)

        # 填写邮箱密码
        try:
            email_el = await tab.find(tag_name="input", name="email", timeout=5)
        except:
            email_el = await tab.find(placeholder="请输入邮箱地址", timeout=5)
        await email_el.click()
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()

        try:
            pass_el = await tab.find(tag_name="input", name="password", timeout=5)
        except:
            pass_el = await tab.find(placeholder="请输入登录密码", timeout=5)
        await pass_el.click()
        await pass_el.type_text(PASSWORD, humanize=True)

        captcha = await fill_captcha(tab)
        if not captcha:
            log.warning("验证码识别失败，重试")
            continue

        # 点击登录
        login_btn = await tab.find(css="button.btn.btn-primary", timeout=5)
        await login_btn.click()
        log.info("已点击登录，立即检查跳转...")

        # ✅ 核心修复：不再死等5秒，而是快速轮询 URL
        if await wait_for_url_contains(tab, "/clientarea", 8):
            log.info("✅ 登录成功")
            await take_screenshot(browser, tab, "02_login_success")
            await save_session_cookies(tab)
            return True

        log.warning("登录后未跳转，重试")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("前往签到页...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(SIGN_PAGE)
    except:
        await tab.go_to(SIGN_PAGE)

    # ✅ 等待签到按钮出现，而不是死等3秒
    if not await wait_for_element_by_text(tab, "我要签到", 10):
        log.info("可能已经签到过了")
        await take_screenshot(browser, tab, "02_sign_skip")
        return None

    btn = await tab.find(tag_name="button", text="我要签到", timeout=5)
    await btn.click()
    await asyncio.sleep(1)

    # 数学题
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
        await tab.find(tag_name="button", text="验证答案", timeout=5).click()
        await asyncio.sleep(1)

    for _ in range(2):
        try:
            ok = await tab.find(tag_name="button", text="确定", timeout=2)
            await ok.click()
            await asyncio.sleep(0.5)
        except:
            break

    log.info("签到完成")
    await take_screenshot(browser, tab, "03_sign_complete")
    text = await get_text(tab)
    bal = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text)
    return bal.group(1) if bal else None

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    await tab.go_to(USER_CENTER)
    await asyncio.sleep(2)
    text = await get_text(tab)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        return
    expiry_str = match.group(1)
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {expiry_str}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return

    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=5)
        await renew_btn.click()
        await asyncio.sleep(1)
        await tab.find(tag_name="button", text="立即续费", timeout=5).click()
        await asyncio.sleep(1)
        await tab.find(tag_name="button", text="立即支付", timeout=5).click()
        await asyncio.sleep(1)
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
        log.info("✅ 续费完成")
        await take_screenshot(browser, tab, "04_renew_complete")
    except:
        log.warning("续费流程异常")

async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            wxpush("❌ 登录失败")
            return
        balance = await sign(browser, tab)
        await renew(browser, tab)
        lines = ["✅ 签到成功"]
        if balance:
            lines.append(f"账户余额 {balance} 积分")
        wxpush("\n".join(lines))
    except Exception as e:
        log.exception(e)
        await take_screenshot(browser, tab, "99_error")
    finally:
        await browser.__aexit__(None, None, None)

if __name__ == "__main__":
    asyncio.run(main())
