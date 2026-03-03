"""Quick diagnostic: Discover kebab menu items on Rise 360 lesson outline."""
import time
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
import config

OUTPUT_DIR = Path("data/debug_captures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def login(page):
    page.goto(config.RISE_BASE_URL)
    time.sleep(3)
    try:
        page.locator("button.osano-cm-accept-all").click(timeout=3000)
        time.sleep(1)
    except: pass
    try:
        email = page.locator("input[name='username'], input[type='email'], #email")
        email.first.wait_for(state="visible", timeout=10000)
        email.first.fill(config.EMAIL)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(3)
    except: pass
    try:
        pwd = page.locator("input[name='password'], input[type='password']")
        pwd.first.wait_for(state="visible", timeout=10000)
        pwd.first.fill(config.PASSWORD)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(5)
    except: pass
    page.wait_for_url("**/rise.articulate.com/**", timeout=30000)
    print("Login OK")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=30, args=config.BROWSER_ARGS)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                   locale=config.BROWSER_LOCALE,
                                   timezone_id=config.BROWSER_TIMEZONE)
        ctx.set_default_timeout(30000)
        page = ctx.new_page()
        login(page)

        # Navigate to template
        print("Navigating to template...")
        page.goto(config.TEMPLATE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)
        for _ in range(20):
            loading = page.locator("text='Your content is loading.'")
            if loading.count() == 0 or not loading.first.is_visible(timeout=500):
                break
            time.sleep(2)
        page.wait_for_selector("a:has-text('Edit Content')", timeout=60000)
        time.sleep(3)

        # Get all lessons
        edit_links = page.locator("a:has-text('Edit Content')")
        print(f"Lessons found: {edit_links.count()}")

        # Find lesson container for the FIRST lesson
        link = edit_links.first
        parent = link.locator("xpath=ancestor::div[contains(@class,'course-outline-lesson')][1]")
        if parent.count() == 0:
            print("ERROR: No parent container found")
            browser.close()
            return

        container = parent.first
        container.scroll_into_view_if_needed()
        time.sleep(0.3)

        # Step 1: Hover over the lesson to reveal kebab
        print("\n--- Hovering over lesson ---")
        container.hover()
        time.sleep(1.5)
        page.screenshot(path=str(OUTPUT_DIR / "kebab_hover.png"))

        # Step 2: Find all possible kebab/dots buttons
        kebab_selectors = [
            "button.menu__trigger--dots",
            "button.menu__trigger",
            "button[class*='dots']",
            "button[class*='kebab']",
            "button[class*='more']",
            "button[aria-label*='option' i]",
            "button[aria-label*='menu' i]",
        ]

        found_kebab = None
        for sel in kebab_selectors:
            try:
                btns = container.locator(sel)
                if btns.count() > 0:
                    for i in range(btns.count()):
                        btn = btns.nth(i)
                        vis = btn.is_visible(timeout=500)
                        cls = btn.get_attribute("class") or ""
                        aria = btn.get_attribute("aria-label") or ""
                        print(f"  [{sel}][{i}] vis={vis} class='{cls}' aria='{aria}'")
                        if vis and not found_kebab:
                            found_kebab = btn
            except Exception as e:
                pass

        if not found_kebab:
            # Try at page level, near the container
            print("\n  Trying page-level kebab search...")
            all_dots = page.locator("button.menu__trigger--dots:visible")
            print(f"  Page-level dots buttons visible: {all_dots.count()}")
            if all_dots.count() > 0:
                found_kebab = all_dots.first

        if not found_kebab:
            print("\nERROR: No kebab button found")
            browser.close()
            return

        # Step 3: Click the kebab
        print("\n--- Clicking kebab ---")
        found_kebab.click()
        time.sleep(1.5)
        page.screenshot(path=str(OUTPUT_DIR / "kebab_menu_open.png"))

        # Step 4: Capture ALL visible menu items
        menu_selectors = [
            "[role='menuitem']",
            "[role='option']",
            "[role='menu'] button",
            "[role='menu'] a",
            "[role='menu'] li",
            "[role='menu'] div[role]",
            ".menu__list li",
            ".menu__list button",
            ".menu__list a",
            "[class*='menu__item']",
            "[class*='dropdown'] li",
            "[class*='dropdown'] button",
            "[class*='popover'] li",
            "[class*='popover'] button",
        ]

        all_items = {}
        for sel in menu_selectors:
            try:
                items = page.locator(f"{sel}:visible")
                count = items.count()
                if count > 0:
                    texts = []
                    for i in range(count):
                        try:
                            el = items.nth(i)
                            text = el.inner_text().strip()
                            cls = (el.get_attribute("class") or "")[:100]
                            aria = el.get_attribute("aria-label") or ""
                            data_action = el.get_attribute("data-action") or ""
                            texts.append({
                                "text": text,
                                "class": cls,
                                "aria": aria,
                                "data_action": data_action,
                            })
                        except: pass
                    all_items[sel] = texts
                    print(f"\n  [{sel}] {count} items:")
                    for t in texts:
                        print(f"    text='{t['text']}' class='{t['class']}' aria='{t['aria']}' action='{t['data_action']}'")
            except: pass

        # Also get full menu text
        menus = page.locator("[role='menu']:visible")
        if menus.count() > 0:
            menu_text = menus.first.inner_text()
            print(f"\n  [FULL MENU TEXT]: '{menu_text}'")

        # Also try generic: any visible text near the kebab
        print("\n--- All visible text in menus/dropdowns/popovers ---")
        for sel in ["[role='menu']", "[class*='menu__list']", "[class*='popover']", "[class*='dropdown']"]:
            try:
                els = page.locator(f"{sel}:visible")
                for i in range(els.count()):
                    txt = els.nth(i).inner_text().strip()
                    if txt:
                        print(f"  [{sel}]: '{txt}'")
            except: pass

        # Save results
        results = {
            "kebab_found": True,
            "menu_items": all_items,
        }
        with open(OUTPUT_DIR / "kebab_menu_items.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        # Close menu
        page.keyboard.press("Escape")
        time.sleep(0.5)

        print("\n--- Done ---")
        browser.close()

if __name__ == "__main__":
    main()
