"""
debug_selectors.py — Quick diagnostic to capture exact menu texts in Rise 360.
Discovers:
1. Kebab menu items (for lesson duplication)
2. Block library sidebar items (for block type selection)
3. Overlay behavior
"""

import time
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

import config

OUTPUT_DIR = Path("data/debug_captures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Use any existing duplicated course URL (or template)
COURSE_URL = "https://rise.articulate.com/authoring/gLB_t5uQhKjiuOEs8RquJrCpEJ5g9HW_"


def login(page):
    page.goto(config.RISE_BASE_URL)
    time.sleep(3)
    try:
        cookie_btn = page.locator("button.osano-cm-accept-all")
        if cookie_btn.is_visible(timeout=3000):
            cookie_btn.click()
            time.sleep(1)
    except Exception:
        pass
    try:
        email_input = page.locator("input[name='username'], input[type='email'], #email")
        email_input.first.wait_for(state="visible", timeout=10000)
        email_input.first.fill(config.EMAIL)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(3)
    except Exception as e:
        print(f"  Email error: {e}")
    try:
        pwd_input = page.locator("input[name='password'], input[type='password']")
        pwd_input.first.wait_for(state="visible", timeout=10000)
        pwd_input.first.fill(config.PASSWORD)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(5)
    except Exception as e:
        print(f"  Password error: {e}")
    page.wait_for_url("**/rise.articulate.com/**", timeout=30000)
    print("  Login OK")


def dump_visible_elements(page, label, selector):
    """Dump all visible elements matching selector."""
    els = page.locator(selector)
    count = els.count()
    items = []
    for i in range(min(count, 30)):
        try:
            el = els.nth(i)
            if el.is_visible(timeout=300):
                info = {
                    "index": i,
                    "tag": el.evaluate("el => el.tagName"),
                    "class": (el.get_attribute("class") or "")[:200],
                    "role": el.get_attribute("role") or "",
                    "aria_label": el.get_attribute("aria-label") or "",
                    "text": el.inner_text()[:150].strip().replace("\n", " | "),
                    "data_testid": el.get_attribute("data-testid") or "",
                }
                items.append(info)
        except Exception:
            pass
    print(f"\n  [{label}] {len(items)}/{count} visible elements:")
    for item in items:
        print(f"    [{item['index']}] <{item['tag']}> role='{item['role']}' "
              f"aria='{item['aria_label']}' text='{item['text'][:80]}'")
        if item['class']:
            print(f"         class='{item['class'][:100]}'")
    return items


def navigate_to_course(page):
    """Navigate to the template course."""
    url = config.TEMPLATE_URL
    print(f"  Navigating to: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)

    # Wait for loading to finish
    for _ in range(30):
        loading = page.locator("text='Your content is loading.'")
        if loading.count() == 0 or not loading.first.is_visible(timeout=500):
            break
        time.sleep(2)

    # Wait for outline to appear
    try:
        page.wait_for_selector(
            "a:has-text('Edit Content'), div.course-outline-lesson",
            timeout=60000
        )
        time.sleep(3)
    except Exception:
        time.sleep(5)

    print(f"  At URL: {page.url}")
    page.screenshot(path=str(OUTPUT_DIR / "course_loaded.png"))


def test_kebab_menu(page):
    """Test 1: Open kebab menu and capture all menu items."""
    print("\n" + "=" * 60)
    print("TEST 1: KEBAB MENU ITEMS")
    print("=" * 60)

    navigate_to_course(page)

    page.screenshot(path=str(OUTPUT_DIR / "outline_state.png"))

    # Find lesson containers
    lessons = page.locator("div.course-outline-lesson")
    print(f"\n  Lesson containers: {lessons.count()}")

    # Dump all menu trigger buttons
    dump_visible_elements(page, "MENU_TRIGGERS", "button.menu__trigger")

    # Try to open kebab on lesson 0
    lesson = lessons.first
    lesson.scroll_into_view_if_needed()
    time.sleep(0.3)
    lesson.hover()
    time.sleep(1)

    # Screenshot with hover state
    page.screenshot(path=str(OUTPUT_DIR / "lesson_hovered.png"))

    # Try clicking the kebab
    kebab_sels = [
        "button.menu__trigger--dots",
        "button.menu__trigger",
    ]
    for sel in kebab_sels:
        try:
            btn = lesson.locator(sel).first
            if btn.is_visible(timeout=2000):
                print(f"\n  Clicking kebab: {sel}")
                btn.click()
                time.sleep(1)

                page.screenshot(path=str(OUTPUT_DIR / "kebab_menu_open.png"))

                # Dump ALL visible menu items, buttons, roles
                dump_visible_elements(page, "ROLE_MENUITEM", "[role='menuitem']")
                dump_visible_elements(page, "ROLE_OPTION", "[role='option']")
                dump_visible_elements(page, "ROLE_MENU_CHILDREN", "[role='menu'] > *")
                dump_visible_elements(page, "ALL_MENU_ITEMS",
                    "[role='menu'] li, [role='menu'] button, [role='menu'] a, "
                    "[role='menu'] div[role]")

                # Also try getting all text within any menu/dropdown
                menus = page.locator("[role='menu']")
                if menus.count() > 0:
                    menu_text = menus.first.inner_text()
                    print(f"\n  [MENU_TEXT]: '{menu_text}'")

                # Also check for popovers/dropdowns
                dump_visible_elements(page, "POPOVER",
                    "[class*='popover']:visible, [class*='dropdown']:visible, "
                    "[class*='menu__list']:visible")

                page.keyboard.press("Escape")
                time.sleep(0.5)
                break
        except Exception as e:
            print(f"  Error with {sel}: {e}")

    # Also try the add-lesson button
    dump_visible_elements(page, "ADD_LESSON_BTNS",
        "button.course-outline-lesson__add-above-button, "
        "button[aria-label*='Insert' i], button[aria-label*='lesson' i]")


def test_block_library(page):
    """Test 2: Open block library and capture all block type items."""
    print("\n" + "=" * 60)
    print("TEST 2: BLOCK LIBRARY ITEMS")
    print("=" * 60)

    # Navigate back to course outline
    navigate_to_course(page)

    # Open first lesson editor
    edit_links = page.locator("a:has-text('Edit Content')")
    count = edit_links.count()
    print(f"\n  Edit Content links: {count}")
    if count == 0:
        print("  ERROR: No edit content links")
        return

    edit_links.first.click()
    time.sleep(5)
    page.wait_for_selector("[class*='block-wrapper']", timeout=30000)
    time.sleep(2)

    # Find "+" buttons
    create_btns = page.locator("button.block-create__button")
    print(f"\n  Block create buttons: {create_btns.count()}")

    # Click the LAST "+" button (after last block)
    if create_btns.count() > 1:
        btn = create_btns.nth(create_btns.count() - 1)
        btn.scroll_into_view_if_needed()
        time.sleep(0.3)

        # Hover parent div first
        try:
            parent = btn.locator("xpath=ancestor::div[contains(@class,'block-create')][1]")
            if parent.count() > 0:
                parent.first.hover()
                time.sleep(0.5)
        except Exception:
            pass

        print("  Clicking '+' button...")
        btn.click(force=True)
        time.sleep(2)

        page.screenshot(path=str(OUTPUT_DIR / "block_library_open.png"))

        # Check if a quick menu appeared
        dump_visible_elements(page, "ROLE_OPTION", "[role='option']")
        dump_visible_elements(page, "ROLE_MENUITEM", "[role='menuitem']")
        dump_visible_elements(page, "ROLE_LISTITEM_VIS", "[role='listitem']:visible")

        # Check if block library sidebar appeared
        sidebar = page.locator(".blocks-sidebar__container--library, .blocks-sidebar")
        if sidebar.count() > 0 and sidebar.first.is_visible(timeout=2000):
            print("\n  Block Library sidebar IS visible!")
            sidebar_text = sidebar.first.inner_text()
            print(f"  Sidebar text: '{sidebar_text[:500]}'")

            # Dump preview-dropdown-button items
            dump_visible_elements(page, "PREVIEW_DROPDOWNS",
                "div.preview-dropdown-button")

            # Dump all buttons in sidebar
            dump_visible_elements(page, "SIDEBAR_BUTTONS",
                ".blocks-sidebar button:visible")
        else:
            print("\n  Block Library sidebar NOT visible")

            # Maybe it's a quick popup?
            dump_visible_elements(page, "ALL_VISIBLE_MENUS",
                "[role='menu']:visible, [class*='popover']:visible, "
                "[class*='menu__list']:visible")

            # Dump generic: any new visible elements
            dump_visible_elements(page, "ALL_VISIBLE_BUTTONS", "button:visible")

        # Check overlay
        overlay = page.locator(".blocks-sidebar__overlay--active")
        print(f"\n  Overlay active: {overlay.count() > 0}")
        if overlay.count() > 0:
            print("  Clicking overlay to dismiss...")
            overlay.first.click(force=True)
            time.sleep(0.5)
            print(f"  Overlay still active: {page.locator('.blocks-sidebar__overlay--active').count() > 0}")

        page.keyboard.press("Escape")
        time.sleep(0.5)


def test_rename_lesson(page):
    """Test 3: Try to find the lesson title textarea."""
    print("\n" + "=" * 60)
    print("TEST 3: LESSON RENAME")
    print("=" * 60)

    navigate_to_course(page)

    # Find textareas, inputs, and contenteditable elements in the outline
    dump_visible_elements(page, "TEXTAREAS", "textarea:visible")
    dump_visible_elements(page, "INPUTS", "input:visible")
    dump_visible_elements(page, "CONTENTEDITABLE", "[contenteditable='true']:visible")

    # Find lesson title elements
    lessons = page.locator("div.course-outline-lesson")
    if lessons.count() > 0:
        lesson = lessons.first
        # Check for title elements within the lesson
        dump_visible_elements(page, "LESSON_CHILDREN",
            "div.course-outline-lesson:first-of-type textarea, "
            "div.course-outline-lesson:first-of-type input, "
            "div.course-outline-lesson:first-of-type [contenteditable='true'], "
            "div.course-outline-lesson:first-of-type h2, "
            "div.course-outline-lesson:first-of-type h3, "
            "div.course-outline-lesson:first-of-type span")


def main():
    print("=" * 60)
    print("DEBUG SELECTORS — Rise 360 Menu & Library Diagnostic")
    print("=" * 60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=30,
            args=config.BROWSER_ARGS,
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale=config.BROWSER_LOCALE,
            timezone_id=config.BROWSER_TIMEZONE,
        )
        context.set_default_timeout(30000)
        page = context.new_page()

        print("\n[Login]")
        login(page)

        test_kebab_menu(page)
        test_block_library(page)
        test_rename_lesson(page)

        # Save results
        results = {
            "course_url": COURSE_URL,
            "tests_completed": True,
        }
        with open(OUTPUT_DIR / "debug_selectors.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 60)
        print("DIAGNOSTIC COMPLETE")
        print(f"Screenshots saved to: {OUTPUT_DIR}/")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    main()
