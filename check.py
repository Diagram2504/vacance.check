import json
import os
import re
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright

USER_ID = os.environ["ICU_USER_ID"]
PASSWORD = os.environ["ICU_PASSWORD"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
SEND_MODE = os.environ.get("SEND_MODE", "me")

TARGETS_FILE = Path(__file__).with_name("targets.json")


def load_targets() -> list[dict[str, str]]:
    if not TARGETS_FILE.exists():
        return []
    data = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    return [item for item in data if item.get("reg_no")]


def send_line_message(message: str) -> None:
    if SEND_MODE == "all":
        url = "https://api.line.me/v2/bot/message/broadcast"
        payload = {"messages": [{"type": "text", "text": message}]}
        mode_text = "全員(Broadcast)"
    else:
        url = "https://api.line.me/v2/bot/message/push"
        payload = {
            "to": LINE_USER_ID,
            "messages": [{"type": "text", "text": message}],
        }
        mode_text = "個人(Push)"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=15)
    if response.status_code == 200:
        print(f">>> [LINE] {mode_text} への送信に成功しました。")
    else:
        print(f">>> [LINE] 送信失敗({mode_text}): {response.status_code} {response.text}")


def check_task() -> bool:
    targets = load_targets()
    if not targets:
        print("監視対象の科目が targets.json にありません。")
        return False

    login_url = "https://course-reg.icu.ac.jp/cgi-bin/login"
    found_any = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            # ログイン
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            inputs = page.locator("input")
            inputs.nth(0).press_sequentially(USER_ID, delay=100)
            inputs.nth(1).press_sequentially(PASSWORD, delay=100)
            page.locator('input[value="Start"]').first.click()
            page.wait_for_load_state("networkidle")

            # デバッグ出力（ログイン後の画面状態を確認）
            print("Current URL after Start:", page.url)
            print("Frames:", [f.url for f in page.frames])
            for idx, f in enumerate(page.frames):
                try:
                    btns = f.get_by_role("button").all_inner_texts()
                    inps = f.locator("input").evaluate_all(
                        "els => els.map(e => ({type: e.type, value: e.value, name: e.name}))"
                    )
                    links = f.locator("a").all_inner_texts()
                    print(
                        f"Frame[{idx}] ({f.url}) buttons={btns} inputs={inps} links={links[:10]}"
                    )
                except Exception as e:
                    print(f"Frame[{idx}] inspect error: {e}")

            # 全フレームから Enter を探してクリック
            clicked_enter = False
            for f in page.frames:
                loc = f.locator(
                    'input[value="Enter"], button:has-text("Enter"), a:has-text("Enter")'
                )
                if loc.count() > 0:
                    loc.first.click(timeout=10000)
                    clicked_enter = True
                    break

            if not clicked_enter:
                raise RuntimeError(f"'Enter' control not found on page: {page.url}")

            page.wait_for_load_state("networkidle")

            # フレーム内から BUS(☆) をクリック
            for frame in page.frames:
                star = frame.locator("a[href*='BUS.html']").filter(has_text="☆")
                if star.count() > 0:
                    star.first.click()
                    break
            page.wait_for_timeout(3000)

            # 空き判定（targets.json の各科目を順に確認）
            for frame in page.frames:
                has_any_row = any(
                    frame.locator("tr", has_text=t["reg_no"]).count() > 0
                    for t in targets
                )
                if not has_any_row:
                    continue

                for target in targets:
                    reg_no = target["reg_no"]
                    name = target.get("name", "")
                    row = frame.locator("tr", has_text=reg_no)
                    if row.count() == 0:
                        print(f"[{reg_no} {name}] 該当行が見つかりませんでした。")
                        continue

                    text = row.first.locator("td").last.inner_text()
                    nums = re.findall(r"\d+", text)
                    left = int(nums[0]) if nums else 0

                    if left >= 1:
                        print(f"!!! 空き発見 ({reg_no} {name}): {left}席 !!!")
                        label = f"{reg_no} {name}".strip()
                        send_line_message(
                            f"【ICU空き通知】\n空きが出ました！\n科目: {label}\n現在の残席: {left}"
                        )
                        found_any = True
                    else:
                        print(f"[{reg_no} {name}] 判定：満席 ({left})")
                break
        finally:
            browser.close()

    return found_any


if __name__ == "__main__":
    check_task()
