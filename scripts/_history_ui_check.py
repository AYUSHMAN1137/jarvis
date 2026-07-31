"""One-off browser check for the Conversation History drawer.

Read-only against real chats: it opens the drawer, searches, opens a
conversation, and opens (then cancels) the rename and delete dialogs. It never
confirms a delete and never renames.
"""

import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000/jarvis/"
SHOTS = "data/_history_ui_shots"


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # The app declares no favicon, so Chrome logs a /favicon.ico 404 on every
        # page load. Pre-existing and unrelated to history -- ignore it.
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "favicon" not in m.text
                and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        failed = []
        page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # Dialogs and overlay must start hidden (the [hidden] vs display fix).
        for sel in ["#history-dialog-backdrop", "#history-overlay", "#history-search-clear"]:
            if page.locator(sel).is_visible():
                errors.append(f"{sel} visible before interaction")

        # Temporal grouping is calendar-day based, not elapsed-hours based.
        grouping = page.evaluate("""() => {
            const mk = (dayOffset, hour) => {
                const d = new Date();
                d.setDate(d.getDate() + dayOffset);
                d.setHours(hour, 0, 0, 0);
                return d.toISOString();
            };
            return {
                nowIsToday:        historyGroupFor(mk(0, new Date().getHours())),
                lateYesterday:     historyGroupFor(mk(-1, 22)),
                earlyYesterday:    historyGroupFor(mk(-1, 1)),
                threeDaysAgo:      historyGroupFor(mk(-3, 12)),
                tenDaysAgo:        historyGroupFor(mk(-10, 12)),
                sixtyDaysAgo:      historyGroupFor(mk(-60, 12)),
                garbage:           historyGroupFor('not-a-date'),
            };
        }""")
        expected = {
            "nowIsToday": "Today",
            "lateYesterday": "Yesterday",
            "earlyYesterday": "Yesterday",
            "threeDaysAgo": "Previous 7 Days",
            "tenDaysAgo": "Previous 30 Days",
            "sixtyDaysAgo": "Older",
            "garbage": "Older",
        }
        for key, want in expected.items():
            got = grouping.get(key)
            if got != want:
                errors.append(f"grouping {key}: expected {want!r}, got {got!r}")
        print("grouping checks:", "ok" if all(
            grouping.get(k) == v for k, v in expected.items()) else "FAILED")

        # A conversation switch must not leak the previous turn's telemetry.
        if page.locator("#activity-panel.open").count():
            errors.append("activity panel unexpectedly open before interaction")

        page.click("#history-toggle")
        page.wait_for_timeout(1200)
        if not page.locator("#history-panel.open").count():
            errors.append("history panel did not open")
        # On desktop the drawer must not cover the composer.
        if page.locator("#history-overlay").is_visible():
            errors.append("overlay dims/blocks the chat on desktop")
        if not page.locator("#message-input").is_enabled():
            errors.append("composer not usable while drawer is open on desktop")

        items = page.locator(".history-item")
        count = items.count()
        print(f"conversations rendered: {count}")
        if count == 0:
            errors.append("no conversations rendered")
        groups = page.locator(".history-group-label").all_inner_texts()
        print("groups:", groups)
        page.screenshot(path=f"{SHOTS}/01-drawer.png")

        # Search
        page.fill("#history-search-input", "weather")
        page.wait_for_timeout(1400)
        found = page.locator(".history-item").count()
        print(f"search 'weather' -> {found}")
        if found == 0:
            errors.append("search returned nothing for 'weather'")
        page.screenshot(path=f"{SHOTS}/02-search.png")

        page.click("#history-search-clear")
        page.wait_for_timeout(1200)

        # Open the first conversation
        first_title = page.locator(".history-item-title").first.inner_text()
        page.locator(".history-item-main").first.click()
        page.wait_for_timeout(1600)
        msgs = page.locator(".message").count()
        print(f"opened {first_title!r} -> {msgs} messages rendered")
        if msgs == 0:
            errors.append("opening a conversation rendered no messages")
        if not page.locator(".history-item.active").count():
            errors.append("active conversation not highlighted")
        page.screenshot(path=f"{SHOTS}/03-opened.png")

        # A search racing a conversation switch must not wedge the switch flag.
        page.locator(".history-item-main").nth(1).click()
        page.fill("#history-search-input", "a")
        page.wait_for_timeout(1800)
        page.click("#history-search-clear")
        page.wait_for_timeout(1200)
        if page.evaluate("() => historyState.switching"):
            errors.append("historyState.switching stuck true after search/switch race")
        # A switch must still work after the race.
        before = page.evaluate("() => sessionId")
        page.locator(".history-item-main").first.click()
        page.wait_for_timeout(1800)
        if page.evaluate("() => sessionId") == before:
            errors.append("conversation switch blocked after search/switch race")

        # Rename dialog (cancel -- no write)
        page.locator(".history-item").first.hover()
        page.locator(".history-item-menu-btn").first.click()
        page.wait_for_timeout(500)
        if not page.locator(".history-item-menu").count():
            errors.append("ellipsis menu did not open")
        page.screenshot(path=f"{SHOTS}/04-menu.png")
        page.click(".history-menu-item:not(.danger)")
        page.wait_for_timeout(600)
        if not page.locator("#history-rename-dialog").is_visible():
            errors.append("rename dialog did not open")
        page.screenshot(path=f"{SHOTS}/05-rename.png")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if page.locator("#history-rename-dialog").is_visible():
            errors.append("Escape did not close rename dialog")

        # Delete dialog (cancel -- nothing is deleted)
        page.locator(".history-item").first.hover()
        page.locator(".history-item-menu-btn").first.click()
        page.wait_for_timeout(400)
        page.click(".history-menu-item.danger")
        page.wait_for_timeout(600)
        if not page.locator("#history-delete-dialog").is_visible():
            errors.append("delete dialog did not open")
        page.screenshot(path=f"{SHOTS}/06-delete-confirm.png")
        page.click("#history-delete-cancel")
        page.wait_for_timeout(500)
        if page.locator("#history-delete-dialog").is_visible():
            errors.append("cancel did not close delete dialog")

        # Opening a conversation must put its id in the address bar.
        open_id = page.evaluate("() => sessionId")
        if f"/c/{open_id}" not in page.url:
            errors.append(f"URL does not carry the session id: {page.url}")

        # Refresh -> the URL (not localStorage) restores the conversation
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        restored = page.locator(".message").count()
        print(f"after refresh -> {restored} messages restored")
        if restored == 0:
            errors.append("active conversation was not restored after refresh")
        if page.evaluate("() => sessionId") != open_id:
            errors.append("refresh did not restore the same session from the URL")
        page.screenshot(path=f"{SHOTS}/07-restored.png")

        # New chat -> URL drops back to the base path; Back returns to the chat.
        page.evaluate("() => newChat()")
        page.wait_for_timeout(600)
        if page.url.rstrip("/").endswith(f"/c/{open_id}"):
            errors.append("new chat did not clear the conversation id from the URL")
        page.go_back()
        page.wait_for_timeout(2000)
        if page.evaluate("() => sessionId") != open_id:
            errors.append("browser Back did not reopen the previous conversation")
        page.screenshot(path=f"{SHOTS}/09-back-nav.png")

        # Mobile drawer
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(800)
        page.click("#history-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{SHOTS}/08-mobile.png")

        browser.close()

    if failed:
        print("\nfailed requests:")
        for f in sorted(set(failed)):
            print(" -", f)

    if errors:
        print("\nPROBLEMS:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("\nALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
