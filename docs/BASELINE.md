# J.A.R.V.I.S — Baseline Health Report

Generated: `2026-07-27T19:39:03`  
Produced by `scripts/verdict_report.py`. Re-run after each milestone and compare.

## 1. Verifier coverage  (tools that can ever be verified)

| family | tools | names |
|---|---|---|
| close | 2 | close_application, kill_process |
| file | 10 | create_folder, delete_file, drive_download, move_path, move_to_trash, screen_region_capture, take_screenshot, unzip_file, write_file, zip_files |
| frontend | 6 | generate_image, open_website, play_on_youtube, search_google, search_youtube, write_content |
| google | 5 | calendar_create, calendar_delete, calendar_update, drive_upload, gmail_send |
| input | 7 | focus_window, mouse_click, press_hotkey, scroll, set_clipboard, type_text, window_action |
| memory | 3 | forget, note_correction, remember |
| none | 10 | cancel_power_action, do_multistep, empty_recycle_bin, hibernate_computer, lock_screen, media_control, restart_computer, shutdown_computer, sign_out, sleep_computer |
| open | 3 | open_application, open_file, open_settings_page |
| query | 27 | app_volume, battery_status, calendar_list, calendar_search, clipboard_history, drive_list, drive_search, find_file, get_clipboard, get_datetime, get_system_status, gmail_inbox, gmail_unread, list_directory, list_open_windows, list_processes, list_wifi_networks, network_info, read_document, read_file, read_screen, read_screen_region, recall, system_resources, ui_diagnostics, ui_list_controls, ui_wait |
| toggle | 8 | airplane_mode, bluetooth_control, camera_control, connect_wifi, mute_volume, set_brightness, set_volume, wifi_control |
| ui | 7 | bluetooth_connect_device, check_for_updates, ui_click, ui_do, ui_scroll, ui_set_toggle, ui_type_into |

VERIFIABLE:   78/88 tools have a real verifier (89%)
BY DESIGN:    10 tool(s) declared unverifiable -> always UNKNOWN, never cached
UNCLASSIFIED: 0 tool(s) with no verifier at all  (good)
88/88 tools declare verification= metadata.

## 2. Verification verdicts on real actions  (memory.db)

| verdict | count | share |
|---|---|---|
| UNKNOWN | 23 | 38% |
| (none recorded) | 14 | 23% |
| PASS | 12 | 20% |
| FAIL | 11 | 18% |

Per tool:
| tool | PASS | FAIL | UNKNOWN | none recorded |
|---|---|---|---|---|
| open_settings_page | 3 | 0 | 5 | 1 |
| do_multistep | 0 | 0 | 2 | 4 |
| play_on_youtube | 0 | 0 | 1 | 4 |
| ui_list_controls | 0 | 4 | 0 | 1 |
| open_application | 3 | 0 | 0 | 1 |
| press_hotkey | 0 | 0 | 4 | 0 |
| ui_do | 2 | 2 | 0 | 0 |
| ui_click | 0 | 3 | 0 | 0 |
| bluetooth_control | 2 | 0 | 0 | 0 |
| check_for_updates | 0 | 0 | 2 | 0 |
| focus_window | 0 | 0 | 2 | 0 |
| media_control | 0 | 0 | 2 | 0 |
| set_volume | 2 | 0 | 0 | 0 |
| type_text | 0 | 0 | 1 | 1 |
| ui_type_into | 0 | 2 | 0 | 0 |
| ui_wait | 0 | 0 | 0 | 2 |
| get_clipboard | 0 | 0 | 1 | 0 |
| list_open_windows | 0 | 0 | 1 | 0 |
| set_clipboard | 0 | 0 | 1 | 0 |
| write_content | 0 | 0 | 1 | 0 |

15 tool(s) have NEVER produced a PASS:
check_for_updates, do_multistep, focus_window, get_clipboard, list_open_windows, media_control, play_on_youtube, press_hotkey, set_clipboard, type_text, ui_click, ui_list_controls, ui_type_into, ui_wait, write_content

## 3. Verified command cache  (command_cache.db)

6 / 500 entries used (1% of budget)
total hits: 2

| trigger | kind | status | hits | fails |
|---|---|---|---|---|
| open night light settings | tool | active | 2 | 0 |
| play at volume 10 | tool | active | 0 | 0 |
| increase volume to 50 | tool | active | 0 | 0 |
| turn off the bluetooth | tool | active | 0 | 0 |
| turn on night light | tool | active | 0 | 0 |
| turn off night light | tool | active | 0 | 0 |

## 4. Storage health  (WAL bloat = last shutdown never checkpointed)

| database | size | WAL | SHM | rows |
|---|---|---|---|---|
| memory.db | 68.0 KB | - | - | facts=2, actions=60, corrections=0 |
| skills.db | 24.0 KB | - | - | skills=1, skill_observations=11 |
| command_cache.db | 16.0 KB | - | - | command_cache=6 |
| user_model.db | 36.0 KB | - | - | um_facts=0, um_aliases=0, um_habits=27 |
| proactive.db | 20.0 KB | - | - | proactive_suggestions=2, proactive_consent=0 |
debug_logs     41 file(s)  668.8 KB
voice_cache   159 file(s)  2.3 MB
chats_data      4 file(s)  10.7 KB
backups         0 file(s)  0 B

[ok] No leftover WAL/SHM files.

## 5. Turn latency  (parsed from data/debug_logs/session_*.log)

| turns | min | p50 | p90 | p99 | max | mean |
|---|---|---|---|---|---|---|
| 56 | 0.00s | 1.65s | 19.65s | 67.35s | 100.13s | 8.28s |

7 turn(s) took 10s or longer (12% of all turns): 100s, 67s, 67s, 45s, 29s, 20s, 19s
