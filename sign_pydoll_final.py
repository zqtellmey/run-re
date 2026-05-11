import asyncio, os, re, logging, random, base64, json
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
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"   # 现在会读取为 false

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)
ocr = ddddocr.DdddOcr(show_ad=False)

# ---------- 异步工具 ----------
async def human_delay(min_s=0.3, max_s=0.9):
    await asyncio.sleep(random.uniform(min_s, max_s))

async def take_screenshot(tab, browser, name):
    """通过 CDP 截图，解决无 screenshot 方法问题"""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{ts}_{name}.png"
        conn = getattr(browser, '_connection', None) or getattr(browser, 'connection', None)
        if conn:
            result = await conn.execute("Page.captureScreenshot", {"format": "png"})
            data = result.get("data", "")
            if data:
                Path(path).write_bytes(base64.b64decode(data))
                log.info(f"📸 截图: {path}")
                return
        # 如果失败，回退到 tab 方法（但 pydoll 可能没有，忽略）
    except Exception as e:
        log.warning(f"截图失败: {e}")

async def get_text(tab):
    try:
        return str(await tab.execute_script("return document.body.innerText"))
    except:
        return ""

# ---------- 浏览器 ----------
async def create_browser():
    options = ChromiumOptions()
    options.headless = HEADLESS
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    browser = await Chrome(options=options).__aenter__()
    tab = await browser.start()
    return browser, tab

# ---------- 登录 ----------
async def login(browser, tab):
    log.info("访问登录页，尝试绕过 Cloudflare...")
    # 直接导航，pydoll 的 bypass 在有头模式下可能自动成功，先尝试
    await tab.go_to(LOGIN_URL)
    await asyncio.sleep(4)

    # 检查是否出现 Cloudflare 验证
    body = await get_text(tab)
    if "verify you are human" in body.lower() or "performing security verification" in body.lower():
        log.info("检测到 Cloudflare 验证，等待自动完成...")
        for _ in range(15):
            body = await get_text(tab)
            if "email" in body:
                break
            # 尝试点击验证框
            await tab.execute_script("""
                (function() {
                    const btn = document.querySelector('iframe')?.contentWindow?.document?.querySelector('#checkbox');
                    if (btn) btn.click();
                })()
            """)
            await asyncio.sleep(2)
        # 最终检查
        await asyncio.sleep(2)

    await take_screenshot(tab, browser, "01_login_page")

    # 填表
    try:
        email_el = await tab.find(tag_name="input", name="email", timeout=10)
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()
        pass_el = await tab.find(tag_name="input", name="password")
        await pass_el.type_text(PASSWORD, humanize=True)
    except Exception:
        await tab.execute_script(f"""
            document.querySelector('input[name="email"]').value='{EMAIL}';
            document.querySelector('input[name="password"]').value='{PASSWORD}';
        """)

    # 识别验证码（同步方式）
    for retry in range(3):
        try:
            img = await tab.find(tag_name="img")
            src = await img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                captcha = ocr.classification(img_bytes)
                captcha = re.sub(r'[^a-zA-Z0-9]', '', captcha)
                log.info(f"验证码: {captcha}")
                captcha_el = await tab.find(tag_name="input", name="captcha", timeout=5)
                await captcha_el.type_text(captcha, humanize=True)
                break
        except Exception as e:
            log.warning(f"验证码重试: {e}")
            await human_delay(1, 1.5)

    # 点击登录
    btn = await tab.find(tag_name="button", text="登录", timeout=10)
    await btn.click()
    await asyncio.sleep(4)

    url = await tab.execute_script("return window.location.href")
    if "/clientarea" in url:
        log.info("✅ 登录成功")
        await take_screenshot(tab, browser, "02_login_success")
        return True
    log.error(f"登录失败，当前 URL: {url}")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("开始签到...")
    await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(3)

    try:
        btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
        await btn.click()
    except:
        log.info("可能已经签到过了")
        await take_screenshot(tab, browser, "02_sign_skip")
        return

    # 数学弹窗
    try:
        text = await get_text(tab)
        match = re.search(r'请计算[：:]\s*(\d+)\s*([+\-*/])\s*(\d+)', text)
        if match:
            a, op, b = int(match[1]), match[2], int(match[3])
            result = a + b if op == '+' else a - b if op == '-' else a * b if op == '*' else a // b
            ans = str(result)
            ans_el = await tab.find(tag_name="input", placeholder="请输入答案", timeout=5)
            await ans_el.type_text(ans, humanize=True)
            verify_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
            await verify_btn.click()
            await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"数学弹窗异常: {e}")

    try:
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
    except:
        pass
    log.info("签到完成")
    await take_screenshot(tab, browser, "03_sign_complete")

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    await tab.go_to(USER_CENTER)
    await asyncio.sleep(3)

    text = await get_text(tab)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日，跳过续费")
        return
    expiry = datetime.strptime(match.group(1), "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {match.group(1)}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return

    try:
        await (await tab.find(tag_name="button", text="续费", timeout=10)).click()
    except:
        return
    await asyncio.sleep(2)
    try:
        await (await tab.find(tag_name="button", text="立即续费", timeout=5)).click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        await (await tab.find(tag_name="button", text="立即支付", timeout=5)).click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        ok_btn = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok_btn.click()
    except:
        pass
    log.info("续费完成")
    await take_screenshot(tab, browser, "04_renew_complete")

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            return
        await sign(browser, tab)
        await renew(browser, tab)
    except Exception as e:
        log.exception(f"任务失败: {e}")
        await take_screenshot(tab, browser, "99_error")
    finally:
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
