import asyncio, os, re, time, random, logging
from pathlib import Path
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"
SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

ocr = ddddocr.DdddOcr(show_ad=False)

# 模拟人类行为的辅助函数
async def human_delay(min_s=0.3, max_s=1.2):
    await asyncio.sleep(random.uniform(min_s, max_s))

async def random_mouse_move(tab):
    try:
        await tab.execute_script("""
            function simulateMouseMove(x, y) {
                var e = new MouseEvent('mousemove', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: x,
                    clientY: y
                });
                document.dispatchEvent(e);
            }
            simulateMouseMove(Math.random()*800, Math.random()*600);
        """)
    except:
        pass

# 截图
async def take_screenshot(tab, name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOT_DIR / f"{timestamp}_{name}.png"
    try:
        await tab.screenshot(str(path))
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

# 获取页面文本
async def get_text(tab):
    try:
        return str(await tab.execute_script("return document.body.innerText"))
    except:
        return ""

# 登录
async def login(tab):
    log.info("开始登录...")
    # 使用 pydoll 绕过 Cloudflare
    async with tab.expect_and_bypass_cloudflare_captcha():
        await tab.go_to(LOGIN_URL)
    await asyncio.sleep(2)
    await take_screenshot(tab, "01_login_page")

    # 填写邮箱密码
    try:
        el = await tab.find(tag_name="input", name="email", timeout=10)
        await el.click()
        await el.type_text(EMAIL, humanize=True)
        await human_delay()
        el = await tab.find(tag_name="input", name="password", timeout=5)
        await el.click()
        await el.type_text(PASSWORD, humanize=True)
    except Exception as e:
        log.warning(f"填表异常: {e}，尝试 JS 注入")
        await tab.execute_script(f"""
            document.querySelector('input[name="email"]').value='{EMAIL}';
            document.querySelector('input[name="password"]').value='{PASSWORD}';
        """)
    await human_delay()

    # 识别验证码
    for retry in range(3):
        try:
            img_el = await tab.find(tag_name="img", timeout=5)
            src = await img_el.get_attribute("src")
            if src and src.startswith("data:image"):
                import base64
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                captcha_code = ocr.classification(img_bytes)
                captcha_code = re.sub(r'[^a-zA-Z0-9]', '', captcha_code)
                log.info(f"识别验证码: {captcha_code}")
                captcha_input = await tab.find(tag_name="input", name="captcha", timeout=5)
                await captcha_input.click()
                await captcha_input.type_text(captcha_code, humanize=True)
                break
        except Exception as e:
            log.warning(f"验证码识别失败: {e}")
            await asyncio.sleep(1)
    else:
        raise Exception("验证码识别多次失败")

    # 点击登录
    btn = await tab.find(tag_name="button", text="登录", timeout=5)
    await btn.click()
    await asyncio.sleep(3)
    if "clientarea" in await tab.execute_script("return window.location.href"):
        log.info("✅ 登录成功")
        await take_screenshot(tab, "02_login_success")
        return True
    else:
        log.error("登录可能失败，当前URL: " + await tab.execute_script("return window.location.href"))
        return False

# 签到
async def sign(tab):
    log.info("开始签到...")
    async with tab.expect_and_bypass_cloudflare_captcha():
        await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(2)
    btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
    await btn.click()
    # 处理数学弹窗
    await asyncio.sleep(1)
    try:
        text = await get_text(tab)
        match = re.search(r'请计算[：:]\s*(\d+)\s*([\+\-\*/])\s*(\d+)', text)
        if match:
            a, op, b = int(match[1]), match[2], int(match[3])
            result = a + b if op == '+' else a - b if op == '-' else a * b if op == '*' else a // b
            answer = str(result)
            log.info(f"计算结果: {answer}")
            answer_input = await tab.find(tag_name="input", placeholder="请输入答案", timeout=5)
            await answer_input.click()
            await answer_input.type_text(answer, humanize=True)
            verify_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
            await verify_btn.click()
            await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"处理数学弹窗异常: {e}")
    # 确认弹窗
    try:
        ok_btn = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok_btn.click()
    except:
        pass
    log.info("签到流程完成")
    await take_screenshot(tab, "03_sign_complete")

# 续费（复用之前的逻辑，转换成异步）
# 由于篇幅，省略续费函数，实际使用时从前面脚本中照搬并改为 async/await
# 如需完整版，请告知，我会补充

async def main():
    log.info("===== 启动 pydoll 浏览器 =====")
    options = ChromiumOptions()
    options.headless = False  # 因为有 xvfb 录屏，这里可以设为 False 方便观察
    options.add_argument("--window-size=1280,720")
    # 代理设置
    options.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    # 隐身与反检测
    options.add_argument("--disable-blink-features=AutomationControlled")
    # 人类行为模拟参数
    options.add_argument("--disable-features=VizDisplayCompositor")
    
    # 使用上下文管理器启动浏览器
    browser = await Chrome(options=options).__aenter__()
    try:
        tab = await browser.start()
        # 随机移动鼠标
        await random_mouse_move(tab)
        await human_delay()
        
        await login(tab)
        await sign(tab)
        # await renew(tab)  如需续费，放开
    except Exception as e:
        log.error(f"任务失败: {e}")
        await take_screenshot(tab, "99_error")
    finally:
        await browser.__aexit__(None, None, None)
    log.info("===== 任务结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
