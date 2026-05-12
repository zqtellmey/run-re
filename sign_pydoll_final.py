import asyncio, os, re, time, logging, random, base64, traceback, json, math
from pathlib import Path
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr
import requests

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

# ---------- 代理连通性检查 ----------
def check_proxy():
    """通过 requests 检查 socks5 代理能否访问目标网站"""
    try:
        session = requests.Session()
        session.proxies = {
            'http': 'socks5h://127.0.0.1:10808',
            'https': 'socks5h://127.0.0.1:10808',
        }
        resp = session.get(BASE_URL, timeout=8)
        log.info(f"代理连通性检查: 状态码 {resp.status_code}")
        return resp.status_code < 500
    except Exception as e:
        log.error(f"代理连通性检查失败: {e}")
        return False

# ---------- 截图 ----------
async def take_screenshot(browser, tab, name):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        await tab.take_screenshot(path=path)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败(take_screenshot): {e}")
        try:
            result = await tab.execute_cdp_command("Page.captureScreenshot", {"format": "png"})
            data = result.get("data", "")
            if data:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = SCREENSHOT_DIR / f"{ts}_{name}.png"
                Path(path).write_bytes(base64.b64decode(data))
                log.info(f"📸 截图(CDP备用): {path}")
        except Exception as e2:
            log.warning(f"截图所有方案失败: {e2}")

async def get_text(tab):
    try:
        result = await tab.execute_script("return document.body.innerText")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def human_delay(min_s=0.2, max_s=0.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------- 探测 Chromium 路径 ----------
def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            log.info(f"找到 Chromium: {p}")
            return p
    try:
        import subprocess
        result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True, timeout=5)
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except:
        pass
    return None

# ---------- 浏览器启动 ----------
async def create_browser():
    opts = ChromiumOptions()
    opts.headless = False
    path = _find_chromium()
    if path:
        opts.binary_location = path
    else:
        log.warning("未找到 Chromium，使用 pydoll 默认路径")

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
async def manual_cf_click(tab, timeout=10):
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

# ---------- 获取并填入验证码 ----------
async def fill_captcha(tab):
    for _ in range(3):
        try:
            cap_img = await tab.find(id="allow_login_email_captcha", timeout=4)
        except:
            cap_img = None
        if not cap_img:
            try:
                cap_img = await tab.find(tag_name="img", alt="验证码", timeout=4)
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
        await asyncio.sleep(0.5)
    return ""

# ---------- 登录 ----------
async def login(browser, tab, max_retries=2):
    for attempt in range(1, max_retries + 1):
        log.info(f"登录尝试 {attempt}/{max_retries}")
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to(LOGIN_URL)
        except Exception:
            await tab.go_to(LOGIN_URL)

        await asyncio.sleep(1.5)
        body = await get_text(tab)
        if "verify you are human" in body.lower():
            if not await manual_cf_click(tab):
                log.warning("Cloudflare 验证可能未完成")

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

        login_btn = await tab.find(css="button.btn.btn-primary", timeout=5)
        await login_btn.click()
        log.info("已点击登录，检查跳转...")

        # 快速轮询检查是否登录成功
        for _ in range(12):
            url = await tab.execute_script("return window.location.href")
            if isinstance(url, dict):
                url = url.get("result", {}).get("result", {}).get("value", "")
            if "/clientarea" in url:
                log.info("✅ 登录成功")
                await take_screenshot(browser, tab, "02_login_success")
                return True
            await asyncio.sleep(0.5)

        log.warning("登录后未跳转，重试")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("前往签到页...")
    await tab.go_to(SIGN_PAGE)
    for _ in range(10):
        body = await get_text(tab)
        if "我要签到" in body:
            break
        await asyncio.sleep(0.5)

    try:
        btn = await tab.find(tag_name="button", text="我要签到", timeout=5)
        await btn.click()
    except:
        log.info("可能已经签到过了")
        await take_screenshot(browser, tab, "02_sign_skip")
        return None

    await asyncio.sleep(0.5)
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
        await asyncio.sleep(0.5)

    for _ in range(2):
        try:
            ok = await tab.find(tag_name="button", text="确定", timeout=3)
            await ok.click()
            await asyncio.sleep(0.3)
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
    await asyncio.sleep(1.5)
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
    # 1. 先检查代理是否可用
    if not check_proxy():
        wxpush("❌ 代理不可用，无法访问目标网站，任务终止")
        log.error("代理连通性检查失败，退出")
        return

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
        await asyncio.sleep(3)
        await browser.__aexit__(None, None, None)

if __name__ == "__main__":
    asyncio.run(main())
