"""Quick diagnostic: How to add a lesson in Rise 360 outline."""
import time
from playwright.sync_api import sync_playwright
import config

TEMPLATE_URL = config.TEMPLATE_URL

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
        page.goto(TEMPLATE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)
        for _ in range(20):
            loading = page.locator("text='Your content is loading.'")
            if loading.count() == 0 or not loading.first.is_visible(timeout=500):
                break
            time.sleep(2)
        page.wait_for_selector("a:has-text('Edit Content')", timeout=60000)
        time.sleep(3)

        before = page.locator("a:has-text('Edit Content')").count()
        print(f"Lessons before: {before}")

        # Scroll to bottom of outline
        page.keyboard.press("End")
        time.sleep(1)
        page.screenshot(path="data/debug_captures/outline_bottom.png")

        # Dump ALL elements at the bottom that could be "add lesson"
        print("\n--- ALL visible elements with 'lesson' or 'add' text ---")
        all_els = page.evaluate("""() => {
            const results = [];
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_ELEMENT
            );
            while (walker.nextNode()) {
                const el = walker.currentNode;
                const text = (el.textContent || '').trim().slice(0, 100);
                const placeholder = el.getAttribute('placeholder') || '';
                const aria = el.getAttribute('aria-label') || '';
                const cls = (el.className || '').toString().slice(0, 150);
                if ((text.toLowerCase().includes('add') && text.toLowerCase().includes('lesson')) ||
                    placeholder.toLowerCase().includes('lesson') ||
                    aria.toLowerCase().includes('lesson') ||
                    cls.includes('add-lesson') ||
                    cls.includes('add-above')) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        tag: el.tagName,
                        cls: cls,
                        text: text.slice(0, 80),
                        placeholder: placeholder,
                        aria: aria,
                        visible: rect.width > 0 && rect.height > 0,
                        y: Math.round(rect.y),
                        contentEditable: el.contentEditable,
                    });
                }
            }
            return results;
        }""")

        for i, el in enumerate(all_els):
            print(f"  [{i}] <{el['tag']}> vis={el['visible']} y={el['y']}")
            print(f"       cls='{el['cls'][:100]}'")
            print(f"       text='{el['text'][:60]}'")
            if el['placeholder']: print(f"       placeholder='{el['placeholder']}'")
            if el['aria']: print(f"       aria='{el['aria']}'")
            if el['contentEditable'] != 'inherit':
                print(f"       contentEditable='{el['contentEditable']}'")

        # Try Strategy: Click "Add a lesson title..." text directly
        print("\n--- Trying to click 'Add a lesson title...' ---")
        try:
            add_text = page.get_by_text("Add a lesson title", exact=False)
            if add_text.count() > 0:
                print(f"  Found {add_text.count()} matches")
                add_text.first.scroll_into_view_if_needed()
                time.sleep(0.3)
                add_text.first.click()
                time.sleep(1)
                page.screenshot(path="data/debug_captures/after_add_click.png")

                # Check what appeared
                print("  After click:")
                inputs = page.locator("input:visible, textarea:visible, [contenteditable='true']:visible")
                for i in range(min(inputs.count(), 10)):
                    try:
                        inp = inputs.nth(i)
                        tag = inp.evaluate("el => el.tagName")
                        cls = (inp.get_attribute("class") or "")[:80]
                        ph = inp.get_attribute("placeholder") or ""
                        print(f"    [{i}] <{tag}> cls='{cls}' placeholder='{ph}'")
                    except: pass

                # Type a title
                page.keyboard.type("Test Lesson Title")
                time.sleep(0.5)
                page.screenshot(path="data/debug_captures/after_typing.png")
                page.keyboard.press("Enter")
                time.sleep(3)

                after = page.locator("a:has-text('Edit Content')").count()
                print(f"  Lessons after: {after} (was {before})")
                page.screenshot(path="data/debug_captures/after_enter.png")
            else:
                print("  NOT FOUND")
        except Exception as e:
            print(f"  Error: {e}")

        # Also try Insert new lesson button
        print("\n--- Trying Insert new lesson button ---")
        add_btns = page.locator("button[aria-label='Insert new lesson']")
        print(f"  Insert new lesson buttons: {add_btns.count()}")
        if add_btns.count() > 0:
            last = add_btns.nth(add_btns.count() - 1)
            vis = last.is_visible(timeout=1000)
            print(f"  Last button visible: {vis}")
            if vis:
                last.scroll_into_view_if_needed()
                time.sleep(0.3)
                page.screenshot(path="data/debug_captures/before_insert_btn.png")
                last.click()
                time.sleep(3)
                after2 = page.locator("a:has-text('Edit Content')").count()
                print(f"  Lessons after insert button: {after2}")
                page.screenshot(path="data/debug_captures/after_insert_btn.png")

        browser.close()

if __name__ == "__main__":
    main()
