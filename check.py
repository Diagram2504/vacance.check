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
STEP_LAG_MS = 1000  # 各ステップのラグ（1秒）


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


def find_and_click_enter(page) -> bool:
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(STEP_LAG_MS)

    selectors = [
        'input[value="Enter"]',
        'input[value*="Enter" i]',
        'button:has-text("Enter")',
        'a:has-text("Enter")',
    ]
    for frame in page.frames:
        for sel in selectors:
            loc = frame.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=10000)
                return True

        role_btn = frame.get_by_role("button", name=re.compile("enter", re.I))
        if role_btn.count() > 0:
            role_btn.first.click(timeout=10000)
            return True

    return False


def click_bus_link(page, bus_keyword: str) -> bool:
    """指定されたカテゴリ（例: BUS, LAなど）の☆リンクをクリック"""
    href_pattern = f"{bus_keyword}.html"
    for frame in page.frames:
        star = frame.locator(f"a[href*='{href_pattern}']").filter(has_text="☆")
        if star.count() > 0:
            star.first.click()
            return True
        # hrefの大小文字の違いに備えた予備検索
        star_any = frame.locator("a").filter(has_text="☆")
        for i in range(star_any.count()):
            el = star_any.nth(i)
            href = el.get_attribute("href") or ""
            if bus_keyword.lower() in href.lower():
                el.click()
                return True
    return False


def check_task() -> None:
    targets = load_targets()
    if not targets:
        print("監視対象の科目が targets.json にありません。")
        return

    # bus設定ごとに科目をグループ化（同じ画面の科目を1度の遷移でチェックするため）
    grouped_targets: dict[str, list[dict[str, str]]] = {}
    for t in targets:
        bus_key = t.get("bus", "BUS").upper()
        grouped_targets.setdefault(bus_key, []).append(t)

    login_url = "https://course-reg.icu.ac.jp/cgi-bin/login"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            inputs = page.locator("input")
            inputs.nth(0).press_sequentially(USER_ID, delay=100)
            inputs.nth(1).press_sequentially(PASSWORD, delay=100)
            page.locator('input[value="Start"]').first.click()
            page.wait_for_timeout(STEP_LAG_MS)

            if not find_and_click_enter(page):
                raise RuntimeError(f"'Enter' が見つかりませんでした。URL: {page.url}")

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(STEP_LAG_MS)

            # グループ（カテゴリ）ごとに画面を開いて判定
            for bus_key, item_list in grouped_targets.items():
                if not click_bus_link(page, bus_key):
                    print(f"[{bus_key}] の☆リンクが見つかりませんでした。")
                    continue

                # 画面読み込みのラグ（1.5秒）
                page.wait_for_timeout(STEP_LAG_MS + 500)

                for frame in page.frames:
                    has_any_row = any(
                        frame.locator("tr", has_text=t["reg_no"]).count() > 0
                        for t in item_list
                    )
                    if not has_any_row:
                        continue

                    page.wait_for_timeout(STEP_LAG_MS)
                    for target in item_list:
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
                        else:
                            print(f"[{reg_no} {name}] 判定：満席 ({left})")
                    break

        finally:
            browser.close()


if __name__ == "__main__":
    check_task()
