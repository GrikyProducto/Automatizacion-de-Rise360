"""
compare_courses.py — Extrae y compara dos cursos Rise 360 bloque por bloque.

Usa RiseAutomation directamente (mismos selectores probados).
"""

import json
import time
import re
import sys
from pathlib import Path

import config
from rise_automation import RiseAutomation
from utils import logger, take_screenshot

SCRIPT_COURSE = "https://rise.articulate.com/authoring/fI7zDtX-AlTgV-F5UHfchFMXic1MLEjj"
HUMAN_COURSE = "https://rise.articulate.com/authoring/UQRGGAk6oiAl4-M3bvjLnTpynuEIgpmI"

OUTPUT_FILE = Path("data/course_comparison.json")
REPORT_FILE = Path("data/comparison_report.txt")


def extract_course_full(rise: RiseAutomation, course_url: str, label: str) -> dict:
    """Extract complete course structure: outline + every lesson's blocks + all text."""
    print(f"\n{'='*60}", flush=True)
    print(f"EXTRAYENDO CURSO: {label}", flush=True)
    print(f"URL: {course_url}", flush=True)
    print(f"{'='*60}", flush=True)

    # Navigate to course outline
    rise.page.goto(course_url, wait_until="domcontentloaded")
    rise._wait_for_content_loaded(max_wait=60)
    time.sleep(3)

    # Get course title
    course_title = rise._get_course_title_from_page()
    print(f"  Titulo: '{course_title}'", flush=True)

    # Get lessons from outline
    lessons_info = rise._get_outline_lessons_info()
    print(f"  Lecciones: {len(lessons_info)}", flush=True)
    for li in lessons_info:
        print(f"    [{li['index']}] '{li['title']}'", flush=True)

    # For each lesson, open editor and extract ALL blocks with ALL text
    lessons_data = []
    for i in range(len(lessons_info)):
        print(f"\n  --- Leccion {i}: '{lessons_info[i]['title'][:50]}' ---", flush=True)

        if not rise.open_lesson_editor(i):
            print(f"  ERROR: No se pudo abrir leccion {i}", flush=True)
            lessons_data.append({
                "index": i,
                "title": lessons_info[i]["title"],
                "blocks": [],
                "error": "could not open"
            })
            continue

        # Extract blocks with full text (clicking each one)
        blocks = extract_all_blocks_with_text(rise, i)
        print(f"  Total bloques extraidos: {len(blocks)}", flush=True)

        lessons_data.append({
            "index": i,
            "title": lessons_info[i]["title"],
            "blocks": blocks,
        })

        # Go back to outline for next lesson
        rise.go_back_to_outline()
        time.sleep(2)

    return {
        "label": label,
        "url": course_url,
        "title": course_title,
        "lessons_count": len(lessons_info),
        "lesson_titles": [li["title"] for li in lessons_info],
        "lessons": lessons_data,
    }


def extract_all_blocks_with_text(rise: RiseAutomation, lesson_idx: int) -> list[dict]:
    """Click every block in the lesson editor, read all editables' text."""
    blocks = []

    wrappers = rise.page.locator("[class*='block-wrapper']")
    count = wrappers.count()
    print(f"  block-wrappers: {count}", flush=True)

    for idx in range(count):
        try:
            wrapper = wrappers.nth(idx)
            if not wrapper.is_visible(timeout=500):
                continue

            # Get block type from CSS class
            cls = wrapper.get_attribute("class") or ""
            block_type = rise._extract_block_type_from_class(cls)

            # Get visible text (before clicking)
            visible_text = ""
            try:
                visible_text = wrapper.inner_text().strip()
            except Exception:
                pass

            # Click to activate and find editables
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.2)
            wrapper.click()
            time.sleep(0.6)

            editables = wrapper.locator("[contenteditable='true']")
            ed_count = editables.count()

            editable_texts = []
            for j in range(ed_count):
                try:
                    ed = editables.nth(j)
                    if ed.is_visible(timeout=500):
                        txt = ed.inner_text().strip()
                        editable_texts.append(txt)
                    else:
                        editable_texts.append("")
                except Exception:
                    editable_texts.append("")

            rise.page.keyboard.press("Escape")
            time.sleep(0.15)

            block = {
                "index": idx,
                "type": block_type,
                "editables_count": ed_count,
                "editable_texts": editable_texts,
                "visible_text": visible_text[:500],
            }
            blocks.append(block)

            # Log summary
            preview = editable_texts[0][:50] if editable_texts else visible_text[:50]
            print(f"    [{idx}] {block_type} ({ed_count}ed): '{preview}...'", flush=True)

        except Exception as e:
            blocks.append({
                "index": idx, "type": "error", "editables_count": 0,
                "editable_texts": [], "visible_text": "", "error": str(e)[:100]
            })
            try:
                rise.page.keyboard.press("Escape")
            except Exception:
                pass

    return blocks


def generate_report(script_data: dict, human_data: dict) -> str:
    """Generate exhaustive comparison report."""
    lines = []
    lines.append("=" * 80)
    lines.append("COMPARACION EXHAUSTIVA: CURSO SCRIPT vs CURSO HUMANO")
    lines.append("=" * 80)
    lines.append("")

    # Titles
    lines.append(f"TITULO SCRIPT: {script_data['title']}")
    lines.append(f"TITULO HUMANO: {human_data['title']}")
    same_title = script_data['title'] == human_data['title']
    lines.append(f"  -> {'IGUALES' if same_title else 'DIFERENTES'}")
    lines.append("")

    # Lessons
    lines.append(f"LECCIONES SCRIPT: {script_data['lessons_count']}")
    lines.append(f"LECCIONES HUMANO: {human_data['lessons_count']}")
    lines.append("")

    # Lesson titles comparison
    lines.append("NOMBRES DE LECCIONES:")
    for i in range(max(len(script_data['lesson_titles']), len(human_data['lesson_titles']))):
        st = script_data['lesson_titles'][i] if i < len(script_data['lesson_titles']) else "(N/A)"
        ht = human_data['lesson_titles'][i] if i < len(human_data['lesson_titles']) else "(N/A)"
        marker = "OK" if st == ht else "DIFF"
        lines.append(f"  [{marker}] Leccion {i}:")
        lines.append(f"    SCRIPT: '{st}'")
        lines.append(f"    HUMANO: '{ht}'")
    lines.append("")

    # Lesson-by-lesson block comparison
    s_lessons = script_data.get("lessons", [])
    h_lessons = human_data.get("lessons", [])
    max_lessons = max(len(s_lessons), len(h_lessons))

    total_diffs = 0

    for i in range(max_lessons):
        lines.append(f"\n{'='*70}")
        s_title = s_lessons[i]["title"] if i < len(s_lessons) else "(N/A)"
        h_title = h_lessons[i]["title"] if i < len(h_lessons) else "(N/A)"
        lines.append(f"LECCION {i}")
        lines.append(f"  Script: '{s_title}'")
        lines.append(f"  Humano: '{h_title}'")
        lines.append(f"{'='*70}")

        s_blocks = s_lessons[i]["blocks"] if i < len(s_lessons) else []
        h_blocks = h_lessons[i]["blocks"] if i < len(h_lessons) else []

        lines.append(f"  Bloques SCRIPT: {len(s_blocks)}")
        lines.append(f"  Bloques HUMANO: {len(h_blocks)}")

        if len(s_blocks) != len(h_blocks):
            total_diffs += 1
            lines.append(f"  >>> DIFF: diferente cantidad de bloques!")

        # Block-by-block
        max_blocks = max(len(s_blocks), len(h_blocks))
        for j in range(max_blocks):
            s_b = s_blocks[j] if j < len(s_blocks) else None
            h_b = h_blocks[j] if j < len(h_blocks) else None

            lines.append(f"\n  --- Bloque {j} ---")

            if s_b:
                lines.append(f"  SCRIPT [{s_b['type']}] ({s_b['editables_count']} editables):")
                for k, t in enumerate(s_b.get("editable_texts", [])):
                    t_display = t.replace('\n', ' | ')
                    lines.append(f"    ed[{k}]: '{t_display[:300]}'")
                    if len(t) > 300:
                        lines.append(f"           ...({len(t)} chars total)")
            else:
                lines.append(f"  SCRIPT: (bloque no existe)")

            if h_b:
                lines.append(f"  HUMANO [{h_b['type']}] ({h_b['editables_count']} editables):")
                for k, t in enumerate(h_b.get("editable_texts", [])):
                    t_display = t.replace('\n', ' | ')
                    lines.append(f"    ed[{k}]: '{t_display[:300]}'")
                    if len(t) > 300:
                        lines.append(f"           ...({len(t)} chars total)")
            else:
                lines.append(f"  HUMANO: (bloque no existe)")

            # Highlight differences
            if s_b and h_b:
                if s_b["type"] != h_b["type"]:
                    total_diffs += 1
                    lines.append(f"  >>> DIFF TIPO: {s_b['type']} vs {h_b['type']}")

                s_texts = s_b.get("editable_texts", [])
                h_texts = h_b.get("editable_texts", [])

                if len(s_texts) != len(h_texts):
                    total_diffs += 1
                    lines.append(f"  >>> DIFF #EDITABLES: {len(s_texts)} vs {len(h_texts)}")

                for k in range(min(len(s_texts), len(h_texts))):
                    s_t = s_texts[k].strip()
                    h_t = h_texts[k].strip()
                    if s_t != h_t:
                        total_diffs += 1
                        # Check type of difference
                        if not s_t and h_t:
                            lines.append(f"  >>> DIFF ed[{k}]: SCRIPT vacio, HUMANO tiene texto")
                        elif s_t and not h_t:
                            lines.append(f"  >>> DIFF ed[{k}]: SCRIPT tiene texto, HUMANO vacio")
                        elif s_t in h_t or h_t in s_t:
                            lines.append(f"  >>> DIFF ed[{k}]: uno es substring del otro")
                            lines.append(f"      S({len(s_t)}): '{s_t[:80]}...'")
                            lines.append(f"      H({len(h_t)}): '{h_t[:80]}...'")
                        else:
                            lines.append(f"  >>> DIFF ed[{k}]: contenido diferente")
                            lines.append(f"      S({len(s_t)}): '{s_t[:80]}...'")
                            lines.append(f"      H({len(h_t)}): '{h_t[:80]}...'")

    lines.append(f"\n\n{'='*80}")
    lines.append(f"TOTAL DIFERENCIAS ENCONTRADAS: {total_diffs}")
    lines.append(f"{'='*80}")

    return "\n".join(lines)


def main():
    print("=" * 60, flush=True)
    print("COMPARADOR DE CURSOS RISE 360", flush=True)
    print("=" * 60, flush=True)

    rise = RiseAutomation()
    rise.start()

    try:
        # Login
        print("\n[1/5] Login...", flush=True)
        rise.login(config.EMAIL, config.PASSWORD)
        print("  Login OK", flush=True)

        # Extract script course
        print("\n[2/5] Extrayendo curso del SCRIPT...", flush=True)
        script_data = extract_course_full(rise, SCRIPT_COURSE, "SCRIPT")

        # Extract human course
        print("\n[3/5] Extrayendo curso del HUMANO...", flush=True)
        human_data = extract_course_full(rise, HUMAN_COURSE, "HUMANO")

        # Save raw data
        print("\n[4/5] Guardando datos...", flush=True)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({"script": script_data, "human": human_data}, f,
                      indent=2, ensure_ascii=False)
        print(f"  JSON: {OUTPUT_FILE}", flush=True)

        # Generate report
        print("\n[5/5] Generando reporte comparativo...", flush=True)
        report = generate_report(script_data, human_data)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  Reporte: {REPORT_FILE}", flush=True)

        # Summary
        print(f"\n{'='*60}", flush=True)
        print("RESUMEN", flush=True)
        print(f"{'='*60}", flush=True)
        s_total = sum(len(l.get("blocks", [])) for l in script_data["lessons"])
        h_total = sum(len(l.get("blocks", [])) for l in human_data["lessons"])
        print(f"Script: {script_data['lessons_count']} lecciones, {s_total} bloques", flush=True)
        print(f"Humano: {human_data['lessons_count']} lecciones, {h_total} bloques", flush=True)

    finally:
        rise.stop()

    print("\nComparacion completada. Revisa:", flush=True)
    print(f"  {OUTPUT_FILE} (datos crudos)", flush=True)
    print(f"  {REPORT_FILE} (reporte)", flush=True)


if __name__ == "__main__":
    main()
