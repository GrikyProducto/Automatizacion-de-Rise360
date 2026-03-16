"""
Diagnostic: understand Rise 360 block editing sidebar.
Login → open lesson → click block → find pencil → click → dump DOM.
"""
import time
from playwright.sync_api import sync_playwright
import config

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    # Login
    print("Logging in...")
    page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
    time.sleep(2)
    email_sel = "input[name='username'], input[type='email'], input[id*='email']"
    page.wait_for_selector(email_sel, timeout=30000)
    page.locator(email_sel).first.fill(config.EMAIL)
    page.locator("button[type='submit']").first.click()
    time.sleep(1)
    pwd_sel = "input[name='password'], input[type='password']"
    try:
        page.wait_for_selector(pwd_sel, timeout=15000)
    except:
        pass
    page.locator(pwd_sel).first.fill(config.PASSWORD)
    page.locator("button[type='submit']").last.click()
    page.wait_for_url("**/rise.articulate.com/**", timeout=30000)
    time.sleep(3)
    print("Logged in!")

    # Use template course
    COURSE_URL = config.TEMPLATE_URL
    print(f"Opening course: {COURSE_URL}")
    page.goto(COURSE_URL, wait_until="domcontentloaded")
    # Wait for content to fully load
    try:
        page.locator("a:has-text('Edit Content')").first.wait_for(
            state="visible", timeout=60_000
        )
    except:
        time.sleep(10)  # Extra wait
    time.sleep(2)

    # Open first lesson's Edit Content
    edit_links = page.locator("a:has-text('Edit Content')")
    count = edit_links.count()
    print(f"Found {count} 'Edit Content' links")
    if count == 0:
        page.screenshot(path="screenshots/diag_no_edit.png")
        browser.close()
        exit()

    edit_links.first.click()
    time.sleep(3)
    # Wait for block wrappers to appear
    try:
        page.locator("[class*='block-wrapper']").first.wait_for(
            state="visible", timeout=30_000
        )
    except:
        time.sleep(5)
    time.sleep(2)

    # Block wrappers
    wrappers = page.locator("[class*='block-wrapper']")
    wcount = wrappers.count()
    print(f"\nFound {wcount} block-wrappers")
    page.screenshot(path="screenshots/diag_01_editor.png")

    # Test on block 2 (usually text/heading)
    TEST_IDX = 2
    wrapper = wrappers.nth(TEST_IDX)
    wrapper.scroll_into_view_if_needed()
    time.sleep(0.3)

    # === STEP 1: Click the block ===
    print(f"\n=== Step 1: Click block {TEST_IDX} ===")
    wrapper.click()
    time.sleep(0.5)
    page.screenshot(path="screenshots/diag_02_clicked.png")

    # === STEP 2: Dump ALL controls visible after clicking ===
    print("\n=== Step 2: Dump all block controls ===")
    controls = page.evaluate("""() => {
        const results = [];
        // All elements with 'block-controls' in class
        document.querySelectorAll('[class*="block-controls"]').forEach(el => {
            results.push({
                tag: el.tagName,
                classes: el.className.toString().substring(0, 150),
                visible: el.offsetParent !== null || getComputedStyle(el).display !== 'none',
                rect: el.getBoundingClientRect(),
                childTags: Array.from(el.children).map(c => c.tagName + '.' + (c.className || '').toString().substring(0, 50)),
            });
        });
        return results;
    }""")
    for c in controls:
        print(f"  {c['tag']} | visible={c['visible']} | cls={c['classes'][:80]}")
        print(f"    rect: top={c['rect']['top']:.0f} left={c['rect']['left']:.0f} w={c['rect']['width']:.0f} h={c['rect']['height']:.0f}")
        print(f"    children: {c['childTags']}")

    # === STEP 3: Hover the block to reveal controls ===
    print("\n=== Step 3: Hover block ===")
    wrapper.hover()
    time.sleep(0.8)
    page.screenshot(path="screenshots/diag_03_hovered.png")

    controls2 = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('[class*="block-controls"]').forEach(el => {
            const vis = el.offsetParent !== null || getComputedStyle(el).display !== 'none';
            if (vis) {
                results.push({
                    tag: el.tagName,
                    classes: el.className.toString().substring(0, 150),
                    rect: el.getBoundingClientRect(),
                    buttons: Array.from(el.querySelectorAll('button')).map(b => ({
                        cls: b.className.substring(0, 80),
                        ariaLabel: b.getAttribute('aria-label'),
                        title: b.getAttribute('title'),
                        text: b.textContent?.trim().substring(0, 30),
                        visible: b.offsetParent !== null,
                    })),
                });
            }
        });
        return results;
    }""")
    print(f"Visible block-controls after hover: {len(controls2)}")
    for c in controls2:
        print(f"  {c['tag']} | cls={c['classes'][:80]}")
        for b in c['buttons']:
            print(f"    BUTTON: cls={b['cls']}, aria={b['ariaLabel']}, title={b['title']}, text='{b['text']}', vis={b['visible']}")

    # === STEP 4: Find the CORRECT pencil button ===
    print("\n=== Step 4: Find pencil (btn-icon--type-content) ===")

    # First click the block to select it (controls may only appear on selection)
    wrapper.click()
    time.sleep(0.5)

    # The correct selector is the content edit button
    content_btn = page.locator("button.block-controls__btn-icon--type-content")
    cb_count = content_btn.count()
    print(f"  btn-icon--type-content count: {cb_count}")
    for i in range(min(cb_count, 5)):
        try:
            vis = content_btn.nth(i).is_visible(timeout=500)
            bb = content_btn.nth(i).bounding_box()
            print(f"    [{i}] visible={vis}, bbox={bb}")
        except Exception as e:
            print(f"    [{i}] error: {e}")

    # === STEP 5: Click the content edit button ===
    # Find the one closest to our block
    print("\n=== Step 5: Clicking content edit button ===")
    wrapper_bb = wrapper.bounding_box()
    print(f"  Block wrapper bbox: {wrapper_bb}")

    # Click the visible content button closest to our wrapper
    clicked = False
    for i in range(cb_count):
        btn = content_btn.nth(i)
        try:
            if btn.is_visible(timeout=500):
                bb = btn.bounding_box()
                if bb and wrapper_bb:
                    # Check if this button is within the wrapper's y range
                    if abs(bb['y'] - wrapper_bb['y']) < 200:
                        print(f"  Clicking content button [{i}] at {bb}")
                        btn.click()
                        clicked = True
                        break
        except:
            continue

    if not clicked and cb_count > 0:
        # Fallback: click first visible one
        for i in range(cb_count):
            try:
                if content_btn.nth(i).is_visible(timeout=500):
                    print(f"  Fallback: clicking content button [{i}]")
                    content_btn.nth(i).click()
                    clicked = True
                    break
            except:
                continue

    time.sleep(2.0)
    page.screenshot(path="screenshots/diag_04_after_content_btn.png")

    # === STEP 6: Dump EVERYTHING that might be a sidebar/panel ===
    print("\n=== Step 6: What opened after pencil click? ===")

    opened = page.evaluate("""() => {
        const results = [];

        // Check sidebar
        document.querySelectorAll('[class*="sidebar"]').forEach(el => {
            results.push({
                type: 'sidebar',
                tag: el.tagName,
                classes: el.className.toString().substring(0, 150),
                visible: el.offsetParent !== null,
                display: getComputedStyle(el).display,
                rect: el.getBoundingClientRect(),
                text: el.textContent?.trim().substring(0, 200),
                editables: el.querySelectorAll("[contenteditable='true']").length,
            });
        });

        // Check dialogs/panels/popovers
        document.querySelectorAll('[role="dialog"], [class*="popover"], [class*="panel"], [class*="dropdown"], [class*="menu--open"], [class*="edit-"]').forEach(el => {
            if (el.offsetParent !== null) {
                results.push({
                    type: 'panel',
                    tag: el.tagName,
                    classes: el.className.toString().substring(0, 150),
                    rect: el.getBoundingClientRect(),
                    text: el.textContent?.trim().substring(0, 200),
                    editables: el.querySelectorAll("[contenteditable='true']").length,
                });
            }
        });

        // Check for any new visible contenteditable
        const ces = document.querySelectorAll("[contenteditable='true']");
        const visibleCes = Array.from(ces).filter(el => el.offsetParent !== null);
        results.push({
            type: 'contenteditable_summary',
            total: ces.length,
            visible: visibleCes.length,
            details: visibleCes.slice(0, 10).map(el => ({
                tag: el.tagName,
                cls: el.className.toString().substring(0, 80),
                parent_cls: el.parentElement?.className?.toString().substring(0, 80) || '',
                text: el.textContent?.trim().substring(0, 60),
                rect: el.getBoundingClientRect(),
            }))
        });

        return results;
    }""")

    for item in opened:
        print(f"\n  TYPE: {item['type']}")
        if item['type'] == 'contenteditable_summary':
            print(f"    Total CEs: {item['total']}, Visible: {item['visible']}")
            for d in item.get('details', []):
                print(f"      {d['tag']}.{d['cls'][:40]} parent={d['parent_cls'][:40]} text='{d['text'][:40]}'")
                print(f"        rect: x={d['rect']['x']:.0f} y={d['rect']['y']:.0f} w={d['rect']['width']:.0f}")
        else:
            print(f"    {item['tag']} cls={item.get('classes','')[:80]}")
            print(f"    visible={item.get('visible')}, display={item.get('display')}")
            r = item.get('rect', {})
            print(f"    rect: x={r.get('x',0):.0f} y={r.get('y',0):.0f} w={r.get('width',0):.0f} h={r.get('height',0):.0f}")
            print(f"    editables: {item.get('editables', 0)}")
            print(f"    text: {item.get('text', '')[:120]}")

    print("\n\nDone. Closing in 5s...")
    time.sleep(5)
    browser.close()
