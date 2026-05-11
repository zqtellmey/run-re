import asyncio
import os
import re
import logging
import random
import base64
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)
ocr = ddddocr.DdddOcr(show_ad=False)

# ---------- 工具函数（借鉴 katabump）----------
async def human_delay(min_s=0.3, max_s=1.0):
    await asyncio.sleep(min_s + random.random() * (max_s - min_s))

async def take_screenshot(browser, tab, name):
    """通过 CDP 截图，兼容 pydoll"""
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
        await tab.screenshot(str(path))
    except Exception as e:
        log.warning(f"截图失败: {e}")

async def get_text(tab):
    try:
        return str(await tab.execute_script("return document.body.innerText"))
    except:
        return ""

# ---------- 浏览器启动（借鉴 katabump 的参数）----------
async def create_browser():
    options = ChromiumOptions()
    options.headless = HEADLESS
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--password-store=basic")
    options.add_argument("--use-mock-keychain")
    options.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    options.browser_preferences = {
        "credentials_enable_service": False,
        "profile": {
            "password_manager_enabled": False,
            "default_content_setting_values": {"notifications": 2, "geolocation": 2},
        },
    }
    browser = await Chrome(options=options).__aenter__()
    tab = await browser.start()
    return browser, tab

# ---------- Cloudflare 验证处理（借鉴 katabump 的 JS 操作）----------
async def wait_for_cloudflare(tab, timeout=15):
    """等待 Cloudflare 验证完成，使用 katabump 中的 JS 方法操作 shadow DOM"""
    log.info("检测到 Cloudflare 验证，尝试自动完成...")
    await asyncio.sleep(2)
    for i in range(timeout):
        body = await get_text(tab)
        if "email" in body.lower() or "登录" in body:
            log.info("Cloudflare 验证已完成")
            return True
        # 尝试点击验证框（katabump 式 JS 操作）
        await tab.execute_script("""
            (function() {
                const checkboxes = document.querySelectorAll('iframe');
                for (let iframe of checkboxes) {
                    try {
                        const innerDoc = iframe.contentDocument || iframe.contentWindow.document;
                        const checkbox = innerDoc.querySelector('#checkbox, input[type="checkbox"]');
                        if (checkbox) {
                            checkbox.focus();
                            checkbox.click();
                            checkbox.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            checkbox.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    } catch(e) {}
                }
            })()
        """)
        await asyncio.sleep(1)
    return False

# ---------- 登录（使用 katabump 的 bypass 上下文）----------
async def login(browser, tab):
    log.info("访问登录页...")
    # ★ 核心：使用 katabump 相同的 Cloudflare 绕过方式
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(LOGIN_URL)
    except Exception:
        await tab.go_to(LOGIN_URL)

    await asyncio.sleep(3)

    # 如果仍然有验证页面，手动处理
    body = await get_text(tab)
    if "verify you are human" in body.lower() or "performing security verification" in body.lower():
        if not await wait_for_cloudflare(tab):
            log.warning("Cloudflare 验证可能未完成，继续尝试登录...")

    await take_screenshot(browser, tab, "01_login_page")

    # 填写邮箱密码（使用 katabump 的混合方式：先尝试 find，失败则 JS 注入）
    try:
        email_el = await tab.find(tag_name="input", name="email", timeout=10)
        await email_el.click()
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()
        pass_el = await tab.find(tag_name="input", name="password")
        await pass_el.click()
        await pass_el.type_text(PASSWORD, humanize=True)
    except Exception:
        await tab.execute_script(f"""
            document.querySelector('input[name="email"]').value='{EMAIL}';
            document.querySelector('input[name="password"]').value='{PASSWORD}';
        """)
        await human_delay()

    # 识别验证码
    for _ in range(3):
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
                await captcha_el.click()
                await captcha_el.type_text(captcha, humanize=True)
                break
        except Exception as e:
            log.warning(f"验证码重试: {e}")
            await asyncio.sleep(1)

    # 点击登录按钮
    btn = await tab.find(tag_name="button", text="登录", timeout=10)
    await btn.click()
    await asyncio.sleep(4)

    url = await tab.execute_script("return window.location.href")
    if "/clientarea" in url:
        log.info("✅ 登录成功")
        await take_screenshot(browser, tab, "02_login_success")
        return True
    log.error(f"登录失败，当前 URL: {url}")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("开始签到...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(SIGN_PAGE)
    except Exception:
        await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(3)

    try:
        btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
        await btn.click()
    except:
        log.info("可能已经签到过了")
        await take_screenshot(browser, tab, "02_sign_skip")
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
            await ans_el.click()
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
    await take_screenshot(browser, tab, "03_sign_complete")

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(USER_CENTER)
    except Exception:
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
    await take_screenshot(browser, tab, "04_renew_complete")

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            return
        await sign(browser, tab)
        await renew(browser, tab)
    except Exception as e:
        log.error(f"任务失败: {e}")
        traceback.print_exc()
        await take_screenshot(browser, tab, "99_error")
    finally:
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
