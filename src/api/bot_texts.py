"""
Localized text strings for the Matrix bot menu system.

Structure: TEXTS[lang_code][key] = string (may contain {placeholders}).
Hebrew strings are TODO placeholders for the localization phase.
"""

TEXTS = {
    'en': {
        # Language selection
        'language_select': (
            "Welcome to KavManager!\n\n"
            "  1. English\n"
            "  2. עברית"
        ),
        'language_saved': "Language set to English.",

        # Main menu
        'main_menu_header': "Hi {name}! What would you like to do?",
        'main_menu_options': (
            "  1. \U0001f4c5 My Schedule\n"
            "  2. \U0001f4c5 Team Schedule\n"
            "  3. \U0001f4cb Tasks\n"
            "  4. \U0001f504 Swap Assignment\n"
            "  5. \u26a0\ufe0f Log Unplanned Task\n"
            "  6. \U0001f4dd Report Issue\n"
            "  7. \U0001f392 My Gear\n"
            "  8. \U0001f392 Team Gear\n"
            "  9. \U0001f4ca My Stats\n"
            "  10. \U0001f310 Change Language"
        ),
        'main_menu_commander': "  {n}. \U0001f4ca Commander Menu",
        'main_menu_notifications': "  {n}. \U0001f514 Notification Settings",

        # Navigation
        'nav_prev': "  1. \u25c0 Previous day",
        'nav_next': "  2. \u25b6 Next day",
        'nav_today': "  3. \U0001f4c5 Today",
        'nav_back': "  0. Back to menu",

        # Schedule
        'my_schedule_header': "Your schedule for {date}:",
        'team_schedule_header': "Team schedule for {date}:",
        'day_header': "\U0001f31e Day:",
        'night_header': "\U0001f319 Night:",
        'no_assignments': "No assignments",
        'assignment_line': "  {task} {start}-{end}",
        'team_assignment_line': "  {task} {start}-{end} \u2014 {soldiers}",

        # Tasks view
        'tasks_header': "Tasks for {date}:",
        'task_covered': "\u2705 {task} {start}-{end} ({filled}/{required} covered)",
        'task_uncovered': "\u26a0\ufe0f {task} {start}-{end} ({filled}/{required} \u2014 UNCOVERED)",

        # Stats
        'stats_header': "Your stats ({mode}):",
        'stats_mode_weighted': "whole reserve period, per present day",
        'stats_mode_absolute': "whole reserve period, absolute hours",
        'stats_total': "\U0001f4ca Total hours: {total}h (unit avg: {avg}h)",
        'stats_day': "\U0001f31e Day: {hours}h ({diff})",
        'stats_night': "\U0001f319 Night: {hours}h ({diff})",
        'stats_rank': "\U0001f4c8 Rank: {rank} most loaded of {total}",
        'stats_toggle_absolute': "  1. Switch to absolute hours",
        'stats_toggle_weighted': "  1. Switch to per-present-day",

        # Change language
        'change_language': (
            "Choose language:\n\n"
            "  1. English\n"
            "  2. \u05e2\u05d1\u05e8\u05d9\u05ea"
        ),

        # Errors / generic
        'invalid_option': "That's not a valid option. Please choose a number:",
        'unrecognized': (
            "You are not registered. Contact your commander to link "
            "your Matrix ID in the desktop app."
        ),
        'coming_soon': "Coming soon!",
        'error': "An error occurred. Try again later.",
        'bot_online': "KavManager bot is online.",
        'bot_offline': "KavManager bot is going offline.",
        'schedule_updated': "Your schedule has been updated:\n",
        'schedule_cleared': "Your upcoming assignments have been cleared.",

        # Swap assignment
        'swap_pick_assignment': (
            "Your upcoming assignments:\n{lines}\n\n"
            "Which assignment to swap? (0 = back)"
        ),
        'swap_no_assignments': "You have no upcoming assignments to swap.",
        'swap_pick_soldier': (
            "Available soldiers:\n{lines}\n\n"
            "Who should replace you? (0 = back)"
        ),
        'swap_no_candidates': "No available soldiers for this assignment window.",
        'swap_waiting': (
            "\U0001f504 Swap request sent to {target}.\n"
            "  {task} {date} {start}-{end}: {requester} \u2192 {target}\n"
            "  Waiting for response ({timeout} min)...\n\n"
            "  0. Cancel swap"
        ),
        'swap_request_incoming': (
            "\U0001f504 {requester} wants to swap with you:\n"
            "  {task} {date} {start}-{end}\n\n"
            "  1. Accept\n"
            "  2. Decline"
        ),
        'swap_accepted': "\u2705 Swap accepted! {task} {date} {start}-{end} is now assigned to {target}.",
        'swap_declined': "\u274c {target} declined the swap request.",
        'swap_declined_target': "\u274c You declined the swap request from {requester}.",
        'swap_cancelled': "\u274c Swap request cancelled.",
        'swap_cancelled_target': "\u274c {requester} cancelled the swap request.",
        'swap_timeout_requester': "\u23f0 {target} didn't respond in {timeout} min. Swap cancelled.",
        'swap_timeout_target': "\u23f0 Swap request from {requester} expired.",
        'swap_complete_commander': (
            "\U0001f504 Swap completed:\n"
            "  {task} {date} {start}-{end}\n"
            "  {old_soldier} \u2192 {new_soldier}"
        ),
        'swap_candidate_line': "  {n}. {name} ({domain} {diff})",

        # My Gear
        'my_gear_header': "Your gear:",
        'my_gear_empty': "You have no gear items.",
        'my_gear_item': "  {n}. {name}{quantity}{serial}",
        'my_gear_actions': (
            "\n  A. Add item\n"
            "  R. Remove item\n"
            "  0. Back to menu"
        ),
        'my_gear_add_name': "Enter item name (0 = cancel):",
        'my_gear_add_quantity': "Quantity (default 1, 0 = cancel):",
        'my_gear_add_serial': "Serial number (press enter to skip, 0 = cancel):",
        'my_gear_added': "\u2705 Added: {name} (\u00d7{quantity}){serial}",
        'my_gear_remove_prompt': "Which item to remove? (0 = cancel)\n{lines}",
        'my_gear_removed': "\u2705 Removed: {name}",

        # Team Gear
        'team_gear_header': "Team gear:",
        'team_gear_empty': "No team gear items.",
        'team_gear_item': "  {n}. {name}{quantity}{serial}",
        'team_gear_actions': (
            "\n  A. Add item\n"
            "  R. Remove item\n"
            "  0. Back to menu"
        ),
        'team_gear_add_name': "Enter item name (0 = cancel):",
        'team_gear_add_quantity': "Quantity (default 1, 0 = cancel):",
        'team_gear_add_serial': "Serial number (press enter to skip, 0 = cancel):",
        'team_gear_added': "\u2705 Added: {name} (\u00d7{quantity}){serial}",
        'team_gear_remove_prompt': "Which item to remove? (0 = cancel)\n{lines}",
        'team_gear_removed': "\u2705 Removed: {name}",
        'team_gear_commander_notify_add': "\U0001f392 {soldier} added team gear: {name} (\u00d7{quantity})",
        'team_gear_commander_notify_remove': "\U0001f392 {soldier} removed team gear: {name}",

        # Report Issue
        'report_issue_prompt': "Describe the issue:",
        'report_issue_done': (
            "\u2705 Issue reported. Commander notified.\n"
            "  \"{description}\"\n\n"
            "  0. Back to menu"
        ),
        'report_issue_commander': "\U0001f4dd {soldier} reported: \"{description}\"",

        # Unplanned task
        'unplanned_warning': (
            "\u26a0\ufe0f Log a task you were called to perform outside\n"
            "the regular schedule (e.g., emergency drone op).\n\n"
            "This creates a new task and affects scheduling.\n"
            "Your commander will be notified.\n"
            "For regular task changes, use Swap instead.\n\n"
            "  1. Continue\n"
            "  0. Back"
        ),
        'unplanned_describe': "Describe the task:",
        'unplanned_start_time': "When did you start? (HH:MM)",
        'unplanned_start_time_invalid': "Invalid time format. Please use HH:MM (24h), e.g. 14:30",
        'unplanned_end_time': (
            "When did you finish or expect to finish? (HH:MM)\n"
            "Or type \"now\" if the task just finished."
        ),
        'unplanned_needs_more': (
            "Are more soldiers needed for this task?\n\n"
            "  1. Just me\n"
            "  2. Yes, specify\n"
            "  0. Cancel"
        ),
        'unplanned_how_many': "How many soldiers total (including you)?",
        'unplanned_roles_prompt': (
            "Does this task require specific roles?\n\n"
            "  1. No, any soldier\n"
            "  2. Yes, select roles\n"
            "  0. Cancel"
        ),
        'unplanned_role_select': "Available roles:\n{lines}\n\nSelect a role (or 0 when done):",
        'unplanned_role_select_with_current': (
            "Roles so far: {current}\n\n"
            "Available roles:\n{lines}\n\n"
            "Select a role (or 0 when done):"
        ),
        'unplanned_role_quantity': "How many {role} needed?",
        'unplanned_confirm': (
            "Logging unplanned task:\n"
            "  {description} \u2014 {date} {start}-{end}\n"
            "  Soldiers: {count} (non-fractionable) | Roles: {roles}\n"
            "  \u26a0\ufe0f You will be assigned. Commander notified.\n\n"
            "  1. Confirm\n"
            "  0. Cancel"
        ),
        'unplanned_created': "\u2705 Unplanned task logged.",
        'unplanned_commander_notify': (
            "\u26a0\ufe0f {name} logged unplanned task: {description} \u2014 {date} {start}-{end}"
        ),
        'unplanned_commander_needs_more': "\nNeeds {count} soldiers ({remaining} more). Consider reconciling.",
        # Commander menu
        'commander_menu': (
            "Commander menu:\n\n"
            "  1. \U0001f4ca Unit Readiness\n"
            "  2. \U0001f4ca Unit Stats\n"
            "  3. \u2795 Create Task\n"
            "  4. \U0001f4cb Create from Template\n"
            "  5. \U0001f504 Reconcile Schedule\n"
            "  0. Back to main menu"
        ),

        # Commander readiness
        'commander_readiness_header': "Readiness for {date}:",
        'commander_readiness_present': "  Present: {present}/{total} soldiers",
        'commander_readiness_role_ok': "  {role} \u2705 ({have}/{need})",
        'commander_readiness_role_warn': "  {role} \u26a0\ufe0f ({have}/{need})",
        'commander_readiness_status_ok': "  Status: READY \u2705",
        'commander_readiness_status_warn': "  Status: NOT READY \u26a0\ufe0f",
        'commander_readiness_nav': (
            "\n  1. Show soldiers\n"
            "  2. \u25c0 Previous day\n"
            "  3. \u25b6 Next day\n"
            "  4. \U0001f4c5 Today\n"
            "  0. Back"
        ),
        'commander_soldiers_header': "Soldiers for {date}:",
        'commander_soldiers_present': "\u2705 Present ({count}):",
        'commander_soldiers_partial': "\U0001f536 Partial ({count}):",
        'commander_soldiers_partial_arrives': "    {name} \u2014 arrives {time}",
        'commander_soldiers_partial_departs': "    {name} \u2014 departs {time}",
        'commander_soldiers_partial_plain': "    {name}",
        'commander_soldiers_absent': "\u274c Absent ({count}):",
        'commander_soldiers_nav': (
            "\n  2. \u25c0 Previous day\n"
            "  3. \u25b6 Next day\n"
            "  4. \U0001f4c5 Today\n"
            "  0. Back"
        ),

        # Commander stats
        'commander_stats_header': "Unit stats ({mode}):",
        'commander_stats_avg': "\U0001f4ca Avg per soldier: {avg}h",
        'commander_stats_most': "\U0001f51d Most loaded: {name} ({hours}h)",
        'commander_stats_least': "\U0001f53b Least loaded: {name} ({hours}h)",
        'commander_stats_spread': "\U0001f4cf Fairness spread: \u00b1{spread}h",
        'commander_stats_toggle_absolute': "  1. Switch to absolute hours",
        'commander_stats_toggle_weighted': "  1. Switch to per-present-day",

        # Commander create task
        'commander_create_name': "Task name (0 = cancel):",
        'commander_create_start': "Start date and time? (DD/MM HH:MM) (0 = cancel):",
        'commander_create_end': "End date and time? (DD/MM HH:MM) (0 = cancel):",
        'commander_create_count': "How many soldiers needed? (0 = cancel):",
        'commander_create_difficulty': "Difficulty (1-5, where 3 is standard):",
        'commander_create_fractionable': (
            "Can this task be split between soldiers in shifts?\n\n"
            "  1. Yes (fractionable)\n"
            "  2. No, same soldiers the whole time"
        ),
        'commander_create_confirm': (
            "Creating task:\n"
            "  {name} \u2014 {start} to {end}\n"
            "  Soldiers: {count} | Difficulty: {difficulty}\n"
            "  Fractionable: {fractionable} | Roles: {roles}\n\n"
            "  1. Confirm\n"
            "  0. Cancel"
        ),
        'commander_create_done': "\u2705 Task created. Run reconcile to assign soldiers?",
        'commander_create_done_options': "  1. Yes\n  0. No, back to menu",
        'commander_create_datetime_invalid': "Invalid format. Use DD/MM HH:MM, e.g. 29/03 14:00",
        'commander_create_count_invalid': "Please enter a number >= 1.",

        # Commander create from template
        'template_list_header': "Select a template:",
        'template_summary': (
            "Template: {name}\n"
            "  Time: {time}\n"
            "  Soldiers: {count}\n"
            "  Difficulty: {difficulty}\n"
            "  Roles: {roles}\n"
            "  Fractionable: {fractionable}"
        ),
        'template_enter_date': "Enter start date (DD/MM or today/tomorrow):\n  0. Back",
        'template_confirm': (
            "Create task?\n\n"
            "  Name: {name}\n"
            "  Start: {start}\n"
            "  End: {end}\n"
            "  Soldiers: {count}\n"
            "  Difficulty: {difficulty}\n"
            "  Roles: {roles}\n"
            "  Fractionable: {fractionable}\n\n"
            "  1. Confirm\n"
            "  0. Cancel"
        ),
        'template_created': "\u2705 Task \"{name}\" created from template.",
        'template_none_saved': "No saved templates. Create templates in the app first.",
        'template_invalid_choice': "Invalid choice. Try again.",

        # Commander reconcile
        'commander_reconcile_warning': (
            "\u26a0\ufe0f This will recalculate all future assignments.\n"
            "Pinned assignments will be kept.\n\n"
            "  1. Confirm\n"
            "  0. Cancel"
        ),
        'commander_reconcile_running': "\u23f3 Running schedule solver...",
        'commander_reconcile_done': "\u2705 Reconcile complete.\n  Tasks covered: {covered}/{total}",
        'commander_reconcile_uncovered': "\n  \u26a0\ufe0f UNCOVERED: {tasks}",

        # Notification settings
        'notification_settings': (
            "Notification settings:\n\n"
            "  1. Soldier reports/issues: {reports}\n"
            "  2. Gear changes: {gear}\n\n"
            "Schedule changes and UNCOVERED alerts are always on.\n\n"
            "  0. Back to menu"
        ),
        'notif_on': "ON",
        'notif_off': "OFF",

        # UNCOVERED alert (always-on for privileged)
        'reconcile_uncovered_alert': "\u26a0\ufe0f UNCOVERED tasks detected: {tasks}",

        # Ordinal suffixes
        'ordinal_1': '{n}st',
        'ordinal_2': '{n}nd',
        'ordinal_3': '{n}rd',
        'ordinal_n': '{n}th',
    },
    'he': {
        # Language selection
        'language_select': (
            "!\u05d1\u05e8\u05d5\u05db\u05d9\u05dd \u05d4\u05d1\u05d0\u05d9\u05dd \u05dc-KavManager\n\n"
            "  1. English\n"
            "  2. \u05e2\u05d1\u05e8\u05d9\u05ea"
        ),
        'language_saved': "\u05d4\u05e9\u05e4\u05d4 \u05e0\u05e7\u05d1\u05e2\u05d4 \u05dc\u05e2\u05d1\u05e8\u05d9\u05ea.",

        # Main menu — TODO: translate
        'main_menu_header': "\u05d4\u05d9\u05d9 {name}! \u05de\u05d4 \u05ea\u05e8\u05e6\u05d4 \u05dc\u05e2\u05e9\u05d5\u05ea?",
        'main_menu_options': (
            "  1. \U0001f4c5 \u05dc\u05d5\u05d7 \u05d6\u05de\u05e0\u05d9\u05dd \u05e9\u05dc\u05d9\n"
            "  2. \U0001f4c5 \u05dc\u05d5\u05d7 \u05d6\u05de\u05e0\u05d9\u05dd \u05e7\u05d1\u05d5\u05e6\u05ea\u05d9\n"
            "  3. \U0001f4cb \u05de\u05e9\u05d9\u05de\u05d5\u05ea\n"
            "  4. \U0001f504 \u05d4\u05d7\u05dc\u05e4\u05ea \u05de\u05e9\u05d9\u05de\u05d4\n"
            "  5. \u26a0\ufe0f \u05d3\u05d9\u05d5\u05d5\u05d7 \u05de\u05e9\u05d9\u05de\u05d4 \u05dc\u05d0 \u05de\u05ea\u05d5\u05db\u05e0\u05e0\u05ea\n"
            "  6. \U0001f4dd \u05d3\u05d9\u05d5\u05d5\u05d7 \u05ea\u05e7\u05dc\u05d4\n"
            "  7. \U0001f392 \u05e6\u05d9\u05d5\u05d3 \u05e9\u05dc\u05d9\n"
            "  8. \U0001f392 \u05e6\u05d9\u05d5\u05d3 \u05e7\u05d1\u05d5\u05e6\u05ea\u05d9\n"
            "  9. \U0001f4ca \u05e1\u05d8\u05d8\u05d9\u05e1\u05d8\u05d9\u05e7\u05d5\u05ea\n"
            "  10. \U0001f310 \u05e9\u05e0\u05d4 \u05e9\u05e4\u05d4"
        ),
        'main_menu_commander': "  {n}. \U0001f4ca \u05ea\u05e4\u05e8\u05d9\u05d8 \u05de\u05e4\u05e7\u05d3",
        'main_menu_notifications': "  {n}. \U0001f514 \u05d4\u05d2\u05d3\u05e8\u05d5\u05ea \u05d4\u05ea\u05e8\u05d0\u05d5\u05ea",

        # Navigation
        'nav_prev': "  1. \u25c0 \u05d9\u05d5\u05dd \u05e7\u05d5\u05d3\u05dd",
        'nav_next': "  2. \u25b6 \u05d9\u05d5\u05dd \u05d4\u05d1\u05d0",
        'nav_today': "  3. \U0001f4c5 \u05d4\u05d9\u05d5\u05dd",
        'nav_back': "  0. \u05d7\u05d6\u05e8\u05d4 \u05dc\u05ea\u05e4\u05e8\u05d9\u05d8",

        # Schedule
        'my_schedule_header': "\u05dc\u05d5\u05d7 \u05d6\u05de\u05e0\u05d9\u05dd \u05dc-{date}:",
        'team_schedule_header': "\u05dc\u05d5\u05d7 \u05d6\u05de\u05e0\u05d9\u05dd \u05e7\u05d1\u05d5\u05e6\u05ea\u05d9 \u05dc-{date}:",
        'day_header': "\U0001f31e \u05d9\u05d5\u05dd:",
        'night_header': "\U0001f319 \u05dc\u05d9\u05dc\u05d4:",
        'no_assignments': "\u05d0\u05d9\u05df \u05de\u05e9\u05d9\u05de\u05d5\u05ea",
        'assignment_line': "  {task} {start}-{end}",
        'team_assignment_line': "  {task} {start}-{end} \u2014 {soldiers}",

        # Tasks view
        'tasks_header': "\u05de\u05e9\u05d9\u05de\u05d5\u05ea \u05dc-{date}:",
        'task_covered': "\u2705 {task} {start}-{end} ({filled}/{required} \u05de\u05d0\u05d5\u05d9\u05e9)",
        'task_uncovered': "\u26a0\ufe0f {task} {start}-{end} ({filled}/{required} \u2014 \u05dc\u05d0 \u05de\u05d0\u05d5\u05d9\u05e9)",

        # Stats — TODO: translate
        'stats_header': "\u05e1\u05d8\u05d8\u05d9\u05e1\u05d8\u05d9\u05e7\u05d5\u05ea ({mode}):",
        'stats_mode_weighted': "\u05db\u05dc \u05ea\u05e7\u05d5\u05e4\u05ea \u05d4\u05de\u05d9\u05dc\u05d5\u05d0\u05d9\u05dd, \u05dc\u05d9\u05d5\u05dd \u05e0\u05d5\u05db\u05d7\u05d5\u05ea",
        'stats_mode_absolute': "\u05db\u05dc \u05ea\u05e7\u05d5\u05e4\u05ea \u05d4\u05de\u05d9\u05dc\u05d5\u05d0\u05d9\u05dd, \u05e9\u05e2\u05d5\u05ea \u05de\u05d5\u05d7\u05dc\u05d8\u05d5\u05ea",
        'stats_total': "\U0001f4ca \u05e1\u05d4\u05f4\u05db \u05e9\u05e2\u05d5\u05ea: {total} (\u05de\u05de\u05d5\u05e6\u05e2: {avg})",
        'stats_day': "\U0001f31e \u05d9\u05d5\u05dd: {hours} ({diff})",
        'stats_night': "\U0001f319 \u05dc\u05d9\u05dc\u05d4: {hours} ({diff})",
        'stats_rank': "\U0001f4c8 \u05d3\u05d9\u05e8\u05d5\u05d2: {rank} \u05de\u05ea\u05d5\u05da {total}",
        'stats_toggle_absolute': "  1. \u05e2\u05d1\u05d5\u05e8 \u05dc\u05e9\u05e2\u05d5\u05ea \u05de\u05d5\u05d7\u05dc\u05d8\u05d5\u05ea",
        'stats_toggle_weighted': "  1. \u05e2\u05d1\u05d5\u05e8 \u05dc\u05dc\u05d9\u05d5\u05dd \u05e0\u05d5\u05db\u05d7\u05d5\u05ea",

        # Change language
        'change_language': (
            "\u05d1\u05d7\u05e8 \u05e9\u05e4\u05d4:\n\n"
            "  1. English\n"
            "  2. \u05e2\u05d1\u05e8\u05d9\u05ea"
        ),

        # Errors / generic
        'invalid_option': "\u05d6\u05d5 \u05dc\u05d0 \u05d0\u05e4\u05e9\u05e8\u05d5\u05ea \u05ea\u05e7\u05d9\u05e0\u05d4. \u05d1\u05d7\u05e8 \u05de\u05e1\u05e4\u05e8:",
        'unrecognized': (
            "\u05d0\u05ea\u05d4 \u05dc\u05d0 \u05e8\u05e9\u05d5\u05dd. \u05e4\u05e0\u05d4 \u05dc\u05de\u05e4\u05e7\u05d3 \u05e9\u05dc\u05da "
            "\u05db\u05d3\u05d9 \u05dc\u05e7\u05e9\u05e8 \u05d0\u05ea \u05d7\u05e9\u05d1\u05d5\u05df \u05d4-Matrix."
        ),
        'coming_soon': "\u05d1\u05e7\u05e8\u05d5\u05d1!",
        'error': "\u05d0\u05d9\u05e8\u05e2\u05d4 \u05e9\u05d2\u05d9\u05d0\u05d4. \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1 \u05de\u05d0\u05d5\u05d7\u05e8 \u05d9\u05d5\u05ea\u05e8.",
        'bot_online': "\u05d1\u05d5\u05d8 KavManager \u05de\u05d7\u05d5\u05d1\u05e8.",
        'bot_offline': "\u05d1\u05d5\u05d8 KavManager \u05de\u05ea\u05e0\u05ea\u05e7.",
        'schedule_updated': "\u05dc\u05d5\u05d7 \u05d4\u05d6\u05de\u05e0\u05d9\u05dd \u05e9\u05dc\u05da \u05e2\u05d5\u05d3\u05db\u05df:\n",
        'schedule_cleared': "\u05d4\u05de\u05e9\u05d9\u05de\u05d5\u05ea \u05d4\u05e7\u05e8\u05d5\u05d1\u05d5\u05ea \u05e9\u05dc\u05da \u05d1\u05d5\u05d8\u05dc\u05d5.",

        # Swap assignment — TODO: translate
        'swap_pick_assignment': (
            "המשימות הקרובות שלך:\n{lines}\n\n"
            "איזו משימה להחליף? (0 = חזרה)"
        ),
        'swap_no_assignments': "אין לך משימות קרובות להחלפה.",
        'swap_pick_soldier': (
            "חיילים זמינים:\n{lines}\n\n"
            "מי יחליף אותך? (0 = חזרה)"
        ),
        'swap_no_candidates': "אין חיילים זמינים לחלון הזמן הזה.",
        'swap_waiting': (
            "\U0001f504 בקשת החלפה נשלחה ל-{target}.\n"
            "  {task} {date} {start}-{end}: {requester} \u2192 {target}\n"
            "  ממתין לתגובה ({timeout} דק')...\n\n"
            "  0. בטל החלפה"
        ),
        'swap_request_incoming': (
            "\U0001f504 {requester} רוצה להחליף איתך:\n"
            "  {task} {date} {start}-{end}\n\n"
            "  1. אשר\n"
            "  2. דחה"
        ),
        'swap_accepted': "\u2705 ההחלפה אושרה! {task} {date} {start}-{end} שויך ל-{target}.",
        'swap_declined': "\u274c {target} דחה את בקשת ההחלפה.",
        'swap_declined_target': "\u274c דחית את בקשת ההחלפה מ-{requester}.",
        'swap_cancelled': "\u274c בקשת ההחלפה בוטלה.",
        'swap_cancelled_target': "\u274c {requester} ביטל את בקשת ההחלפה.",
        'swap_timeout_requester': "\u23f0 {target} לא הגיב תוך {timeout} דק'. ההחלפה בוטלה.",
        'swap_timeout_target': "\u23f0 בקשת ההחלפה מ-{requester} פגה.",
        'swap_complete_commander': (
            "\U0001f504 החלפה בוצעה:\n"
            "  {task} {date} {start}-{end}\n"
            "  {old_soldier} \u2192 {new_soldier}"
        ),
        'swap_candidate_line': "  {n}. {name} ({domain} {diff})",

        # My Gear — TODO: translate
        'my_gear_header': "הציוד שלך:",
        'my_gear_empty': "אין לך פריטי ציוד.",
        'my_gear_item': "  {n}. {name}{quantity}{serial}",
        'my_gear_actions': (
            "\n  A. הוסף פריט\n"
            "  R. הסר פריט\n"
            "  0. חזרה לתפריט"
        ),
        'my_gear_add_name': "שם הפריט (0 = ביטול):",
        'my_gear_add_quantity': "כמות (ברירת מחדל 1, 0 = ביטול):",
        'my_gear_add_serial': "מספר סידורי (Enter לדילוג, 0 = ביטול):",
        'my_gear_added': "\u2705 נוסף: {name} (\u00d7{quantity}){serial}",
        'my_gear_remove_prompt': "איזה פריט להסיר? (0 = ביטול)\n{lines}",
        'my_gear_removed': "\u2705 הוסר: {name}",

        # Team Gear — TODO: translate
        'team_gear_header': "ציוד קבוצתי:",
        'team_gear_empty': "אין פריטי ציוד קבוצתי.",
        'team_gear_item': "  {n}. {name}{quantity}{serial}",
        'team_gear_actions': (
            "\n  A. הוסף פריט\n"
            "  R. הסר פריט\n"
            "  0. חזרה לתפריט"
        ),
        'team_gear_add_name': "שם הפריט (0 = ביטול):",
        'team_gear_add_quantity': "כמות (ברירת מחדל 1, 0 = ביטול):",
        'team_gear_add_serial': "מספר סידורי (Enter לדילוג, 0 = ביטול):",
        'team_gear_added': "\u2705 נוסף: {name} (\u00d7{quantity}){serial}",
        'team_gear_remove_prompt': "איזה פריט להסיר? (0 = ביטול)\n{lines}",
        'team_gear_removed': "\u2705 הוסר: {name}",
        'team_gear_commander_notify_add': "\U0001f392 {soldier} הוסיף ציוד קבוצתי: {name} (\u00d7{quantity})",
        'team_gear_commander_notify_remove': "\U0001f392 {soldier} הסיר ציוד קבוצתי: {name}",

        # Report Issue — TODO: translate
        'report_issue_prompt': "תאר את התקלה:",
        'report_issue_done': (
            "\u2705 התקלה דווחה. המפקד קיבל הודעה.\n"
            "  \"{description}\"\n\n"
            "  0. חזרה לתפריט"
        ),
        'report_issue_commander': "\U0001f4dd {soldier} \u05d3\u05d9\u05d5\u05d5\u05d7: \"{description}\"",

        # Unplanned task — TODO: translate
        'unplanned_warning': (
            "\u26a0\ufe0f \u05d3\u05d5\u05d5\u05d7 \u05de\u05e9\u05d9\u05de\u05d4 \u05e9\u05d1\u05d9\u05e6\u05e2\u05ea \u05de\u05d7\u05d5\u05e5 \u05dc\u05dc\u05d5\u05d7 \u05d4\u05d6\u05de\u05e0\u05d9\u05dd\n"
            "(\u05dc\u05de\u05e9\u05dc \u05d4\u05e4\u05e2\u05dc\u05ea \u05d3\u05e8\u05d5\u05df \u05d7\u05d9\u05e8\u05d5\u05dd).\n\n"
            "\u05d6\u05d4 \u05d9\u05d9\u05e6\u05d5\u05e8 \u05de\u05e9\u05d9\u05de\u05d4 \u05d7\u05d3\u05e9\u05d4 \u05d5\u05d9\u05e9\u05e4\u05d9\u05e2 \u05e2\u05dc \u05d4\u05e9\u05d9\u05d1\u05d5\u05e5.\n"
            "\u05d4\u05de\u05e4\u05e7\u05d3 \u05e9\u05dc\u05da \u05d9\u05e7\u05d1\u05dc \u05d4\u05d5\u05d3\u05e2\u05d4.\n\n"
            "  1. \u05d4\u05de\u05e9\u05da\n"
            "  0. \u05d7\u05d6\u05e8\u05d4"
        ),
        'unplanned_describe': "\u05ea\u05d0\u05e8 \u05d0\u05ea \u05d4\u05de\u05e9\u05d9\u05de\u05d4:",
        'unplanned_start_time': "\u05de\u05ea\u05d9 \u05d4\u05ea\u05d7\u05dc\u05ea? (HH:MM)",
        'unplanned_start_time_invalid': "\u05e4\u05d5\u05e8\u05de\u05d8 \u05dc\u05d0 \u05ea\u05e7\u05d9\u05df. \u05d4\u05e9\u05ea\u05de\u05e9 \u05d1-HH:MM, \u05dc\u05de\u05e9\u05dc 14:30",
        'unplanned_end_time': (
            "\u05de\u05ea\u05d9 \u05e1\u05d9\u05d9\u05de\u05ea \u05d0\u05d5 \u05e6\u05e4\u05d5\u05d9 \u05dc\u05e1\u05d9\u05d9\u05dd? (HH:MM)\n"
            "\u05d0\u05d5 \u05d4\u05e7\u05dc\u05d3 \"now\" \u05d0\u05dd \u05d4\u05de\u05e9\u05d9\u05de\u05d4 \u05d4\u05e1\u05ea\u05d9\u05d9\u05de\u05d4 \u05e2\u05db\u05e9\u05d9\u05d5."
        ),
        'unplanned_needs_more': (
            "\u05e6\u05e8\u05d9\u05da \u05e2\u05d5\u05d3 \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd \u05dc\u05de\u05e9\u05d9\u05de\u05d4 \u05d4\u05d6\u05d5?\n\n"
            "  1. \u05e8\u05e7 \u05d0\u05e0\u05d9\n"
            "  2. \u05db\u05df, \u05e6\u05d9\u05d9\u05df\n"
            "  0. \u05d1\u05d9\u05d8\u05d5\u05dc"
        ),
        'unplanned_how_many': "\u05db\u05de\u05d4 \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd \u05d1\u05e1\u05d4\u05f4\u05db (\u05db\u05d5\u05dc\u05dc \u05d0\u05d5\u05ea\u05da)?",
        'unplanned_roles_prompt': (
            "\u05d4\u05d0\u05dd \u05d4\u05de\u05e9\u05d9\u05de\u05d4 \u05d3\u05d5\u05e8\u05e9\u05ea \u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd \u05de\u05e1\u05d5\u05d9\u05de\u05d9\u05dd?\n\n"
            "  1. \u05dc\u05d0, \u05db\u05dc \u05d7\u05d9\u05d9\u05dc\n"
            "  2. \u05db\u05df, \u05d1\u05d7\u05e8 \u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd\n"
            "  0. \u05d1\u05d9\u05d8\u05d5\u05dc"
        ),
        'unplanned_role_select': "\u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd \u05d6\u05de\u05d9\u05e0\u05d9\u05dd:\n{lines}\n\n\u05d1\u05d7\u05e8 \u05ea\u05e4\u05e7\u05d9\u05d3 (\u05d0\u05d5 0 \u05dc\u05e1\u05d9\u05d5\u05dd):",
        'unplanned_role_select_with_current': (
            "\u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd \u05e2\u05d3 \u05db\u05d4: {current}\n\n"
            "\u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd \u05d6\u05de\u05d9\u05e0\u05d9\u05dd:\n{lines}\n\n"
            "\u05d1\u05d7\u05e8 \u05ea\u05e4\u05e7\u05d9\u05d3 (\u05d0\u05d5 0 \u05dc\u05e1\u05d9\u05d5\u05dd):"
        ),
        'unplanned_role_quantity': "\u05db\u05de\u05d4 {role} \u05e0\u05d3\u05e8\u05e9\u05d9\u05dd?",
        'unplanned_confirm': (
            "\u05de\u05d3\u05d5\u05d5\u05d7 \u05de\u05e9\u05d9\u05de\u05d4 \u05dc\u05d0 \u05de\u05ea\u05d5\u05db\u05e0\u05e0\u05ea:\n"
            "  {description} \u2014 {date} {start}-{end}\n"
            "  \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd: {count} | \u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd: {roles}\n"
            "  \u26a0\ufe0f \u05ea\u05e9\u05d5\u05d1\u05e5 \u05dc\u05de\u05e9\u05d9\u05de\u05d4. \u05d4\u05de\u05e4\u05e7\u05d3 \u05d9\u05e7\u05d1\u05dc \u05d4\u05d5\u05d3\u05e2\u05d4.\n\n"
            "  1. \u05d0\u05e9\u05e8\n"
            "  0. \u05d1\u05d9\u05d8\u05d5\u05dc"
        ),
        'unplanned_created': "\u2705 \u05de\u05e9\u05d9\u05de\u05d4 \u05dc\u05d0 \u05de\u05ea\u05d5\u05db\u05e0\u05e0\u05ea \u05e0\u05e8\u05e9\u05de\u05d4.",
        'unplanned_commander_notify': (
            "\u26a0\ufe0f {name} \u05d3\u05d9\u05d5\u05d5\u05d7 \u05de\u05e9\u05d9\u05de\u05d4 \u05dc\u05d0 \u05de\u05ea\u05d5\u05db\u05e0\u05e0\u05ea: {description} \u2014 {date} {start}-{end}"
        ),
        'unplanned_commander_needs_more': "\n\u05e6\u05e8\u05d9\u05da {count} \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd (\u05e2\u05d5\u05d3 {remaining}). \u05e9\u05e7\u05d5\u05dc \u05dc\u05d4\u05e8\u05d9\u05e5 \u05e9\u05d9\u05d1\u05d5\u05e5.",

        # Commander menu — TODO: translate
        'commander_menu': (
            "\u05ea\u05e4\u05e8\u05d9\u05d8 \u05de\u05e4\u05e7\u05d3:\n\n"
            "  1. \U0001f4ca \u05de\u05d5\u05db\u05e0\u05d5\u05ea \u05d9\u05d7\u05d9\u05d3\u05d4\n"
            "  2. \U0001f4ca \u05e1\u05d8\u05d8\u05d9\u05e1\u05d8\u05d9\u05e7\u05d5\u05ea \u05d9\u05d7\u05d9\u05d3\u05d4\n"
            "  3. \u2795 \u05e6\u05d5\u05e8 \u05de\u05e9\u05d9\u05de\u05d4\n"
            "  4. \U0001f4cb \u05e6\u05d5\u05e8 \u05de\u05ea\u05d1\u05e0\u05d9\u05ea\n"
            "  5. \U0001f504 \u05e9\u05d9\u05d1\u05d5\u05e5 \u05de\u05d7\u05d3\u05e9\n"
            "  0. \u05d7\u05d6\u05e8\u05d4 \u05dc\u05ea\u05e4\u05e8\u05d9\u05d8 \u05e8\u05d0\u05e9\u05d9"
        ),
        'commander_readiness_header': "\u05de\u05d5\u05db\u05e0\u05d5\u05ea \u05dc-{date}:",
        'commander_readiness_present': "  \u05e0\u05d5\u05db\u05d7\u05d9\u05dd: {present}/{total} \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd",
        'commander_readiness_role_ok': "  {role} \u2705 ({have}/{need})",
        'commander_readiness_role_warn': "  {role} \u26a0\ufe0f ({have}/{need})",
        'commander_readiness_status_ok': "  \u05de\u05e6\u05d1: \u05de\u05d5\u05db\u05df \u2705",
        'commander_readiness_status_warn': "  \u05de\u05e6\u05d1: \u05dc\u05d0 \u05de\u05d5\u05db\u05df \u26a0\ufe0f",
        'commander_readiness_nav': (
            "\n  1. \u05d4\u05e6\u05d2 \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd\n"
            "  2. \u25c0 \u05d9\u05d5\u05dd \u05e7\u05d5\u05d3\u05dd\n"
            "  3. \u25b6 \u05d9\u05d5\u05dd \u05d4\u05d1\u05d0\n"
            "  4. \U0001f4c5 \u05d4\u05d9\u05d5\u05dd\n"
            "  0. \u05d7\u05d6\u05e8\u05d4"
        ),
        'commander_soldiers_header': "\u05d7\u05d9\u05d9\u05dc\u05d9\u05dd \u05dc-{date}:",
        'commander_soldiers_present': "\u2705 \u05e0\u05d5\u05db\u05d7\u05d9\u05dd ({count}):",
        'commander_soldiers_partial': "\U0001f536 \u05d7\u05dc\u05e7\u05d9 ({count}):",
        'commander_soldiers_partial_arrives': "    {name} \u2014 \u05de\u05d2\u05d9\u05e2 {time}",
        'commander_soldiers_partial_departs': "    {name} \u2014 \u05e2\u05d5\u05d6\u05d1 {time}",
        'commander_soldiers_partial_plain': "    {name}",
        'commander_soldiers_absent': "\u274c \u05e0\u05e2\u05d3\u05e8\u05d9\u05dd ({count}):",
        'commander_soldiers_nav': (
            "\n  2. \u25c0 \u05d9\u05d5\u05dd \u05e7\u05d5\u05d3\u05dd\n"
            "  3. \u25b6 \u05d9\u05d5\u05dd \u05d4\u05d1\u05d0\n"
            "  4. \U0001f4c5 \u05d4\u05d9\u05d5\u05dd\n"
            "  0. \u05d7\u05d6\u05e8\u05d4"
        ),
        'commander_stats_header': "\u05e1\u05d8\u05d8\u05d9\u05e1\u05d8\u05d9\u05e7\u05d5\u05ea \u05d9\u05d7\u05d9\u05d3\u05d4 ({mode}):",
        'commander_stats_avg': "\U0001f4ca \u05de\u05de\u05d5\u05e6\u05e2 \u05dc\u05d7\u05d9\u05d9\u05dc: {avg}h",
        'commander_stats_most': "\U0001f51d \u05d4\u05db\u05d9 \u05e2\u05de\u05d5\u05e1: {name} ({hours}h)",
        'commander_stats_least': "\U0001f53b \u05d4\u05db\u05d9 \u05e4\u05d7\u05d5\u05ea: {name} ({hours}h)",
        'commander_stats_spread': "\U0001f4cf \u05e4\u05d9\u05d6\u05d5\u05e8 \u05d4\u05d5\u05d2\u05e0\u05d5\u05ea: \u00b1{spread}h",
        'commander_stats_toggle_absolute': "  1. \u05e2\u05d1\u05d5\u05e8 \u05dc\u05e9\u05e2\u05d5\u05ea \u05de\u05d5\u05d7\u05dc\u05d8\u05d5\u05ea",
        'commander_stats_toggle_weighted': "  1. \u05e2\u05d1\u05d5\u05e8 \u05dc\u05dc\u05d9\u05d5\u05dd \u05e0\u05d5\u05db\u05d7\u05d5\u05ea",
        'commander_create_name': "\u05e9\u05dd \u05d4\u05de\u05e9\u05d9\u05de\u05d4 (0 = \u05d1\u05d9\u05d8\u05d5\u05dc):",
        'commander_create_start': "\u05ea\u05d0\u05e8\u05d9\u05da \u05d5\u05e9\u05e2\u05ea \u05d4\u05ea\u05d7\u05dc\u05d4? (DD/MM HH:MM) (0 = \u05d1\u05d9\u05d8\u05d5\u05dc):",
        'commander_create_end': "\u05ea\u05d0\u05e8\u05d9\u05da \u05d5\u05e9\u05e2\u05ea \u05e1\u05d9\u05d5\u05dd? (DD/MM HH:MM) (0 = \u05d1\u05d9\u05d8\u05d5\u05dc):",
        'commander_create_count': "\u05db\u05de\u05d4 \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd \u05e0\u05d3\u05e8\u05e9\u05d9\u05dd? (0 = \u05d1\u05d9\u05d8\u05d5\u05dc):",
        'commander_create_difficulty': "\u05e8\u05de\u05ea \u05e7\u05d5\u05e9\u05d9 (1-5, 3 = \u05e8\u05d2\u05d9\u05dc):",
        'commander_create_fractionable': (
            "\u05d4\u05d0\u05dd \u05d0\u05e4\u05e9\u05e8 \u05dc\u05d7\u05dc\u05e7 \u05d0\u05ea \u05d4\u05de\u05e9\u05d9\u05de\u05d4 \u05dc\u05de\u05e9\u05de\u05e8\u05d5\u05ea?\n\n"
            "  1. \u05db\u05df (\u05e0\u05d9\u05ea\u05df \u05dc\u05d7\u05dc\u05d5\u05e7\u05d4)\n"
            "  2. \u05dc\u05d0, \u05d0\u05d5\u05ea\u05dd \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd \u05dc\u05db\u05dc \u05d4\u05de\u05e9\u05d9\u05de\u05d4"
        ),
        'commander_create_confirm': (
            "\u05d9\u05d5\u05e6\u05e8 \u05de\u05e9\u05d9\u05de\u05d4:\n"
            "  {name} \u2014 {start} \u05e2\u05d3 {end}\n"
            "  \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd: {count} | \u05e7\u05d5\u05e9\u05d9: {difficulty}\n"
            "  \u05e0\u05d9\u05ea\u05df \u05dc\u05d7\u05dc\u05d5\u05e7\u05d4: {fractionable} | \u05ea\u05e4\u05e7\u05d9\u05d3\u05d9\u05dd: {roles}\n\n"
            "  1. \u05d0\u05e9\u05e8\n"
            "  0. \u05d1\u05d9\u05d8\u05d5\u05dc"
        ),
        'commander_create_done': "\u2705 \u05de\u05e9\u05d9\u05de\u05d4 \u05e0\u05d5\u05e6\u05e8\u05d4. \u05dc\u05d4\u05e8\u05d9\u05e5 \u05e9\u05d9\u05d1\u05d5\u05e5?",
        'commander_create_done_options': "  1. \u05db\u05df\n  0. \u05dc\u05d0, \u05d7\u05d6\u05e8\u05d4 \u05dc\u05ea\u05e4\u05e8\u05d9\u05d8",
        'commander_create_datetime_invalid': "\u05e4\u05d5\u05e8\u05de\u05d8 \u05dc\u05d0 \u05ea\u05e7\u05d9\u05df. \u05d4\u05e9\u05ea\u05de\u05e9 \u05d1-DD/MM HH:MM, \u05dc\u05de\u05e9\u05dc 29/03 14:00",
        'commander_create_count_invalid': "\u05d4\u05db\u05e0\u05e1 \u05de\u05e1\u05e4\u05e8 >= 1.",

        # Commander create from template — TODO: translate
        'template_list_header': "Select a template:",
        'template_summary': (
            "Template: {name}\n"
            "  Time: {time}\n"
            "  Soldiers: {count}\n"
            "  Difficulty: {difficulty}\n"
            "  Roles: {roles}\n"
            "  Fractionable: {fractionable}"
        ),
        'template_enter_date': "Enter start date (DD/MM or today/tomorrow):\n  0. Back",
        'template_confirm': (
            "Create task?\n\n"
            "  Name: {name}\n"
            "  Start: {start}\n"
            "  End: {end}\n"
            "  Soldiers: {count}\n"
            "  Difficulty: {difficulty}\n"
            "  Roles: {roles}\n"
            "  Fractionable: {fractionable}\n\n"
            "  1. Confirm\n"
            "  0. Cancel"
        ),
        'template_created': "\u2705 Task \"{name}\" created from template.",
        'template_none_saved': "No saved templates. Create templates in the app first.",
        'template_invalid_choice': "Invalid choice. Try again.",

        'commander_reconcile_warning': (
            "\u26a0\ufe0f \u05d6\u05d4 \u05d9\u05d7\u05e9\u05d1 \u05de\u05d7\u05d3\u05e9 \u05d0\u05ea \u05db\u05dc \u05d4\u05e9\u05d9\u05d1\u05d5\u05e6\u05d9\u05dd \u05d4\u05e2\u05ea\u05d9\u05d3\u05d9\u05d9\u05dd.\n"
            "\u05e9\u05d9\u05d1\u05d5\u05e6\u05d9\u05dd \u05e0\u05e2\u05d5\u05e6\u05d9\u05dd \u05d9\u05d9\u05e9\u05d0\u05e8\u05d5.\n\n"
            "  1. \u05d0\u05e9\u05e8\n"
            "  0. \u05d1\u05d9\u05d8\u05d5\u05dc"
        ),
        'commander_reconcile_running': "\u23f3 \u05de\u05e8\u05d9\u05e5 \u05e9\u05d9\u05d1\u05d5\u05e5...",
        'commander_reconcile_done': "\u2705 \u05e9\u05d9\u05d1\u05d5\u05e5 \u05d4\u05d5\u05e9\u05dc\u05dd.\n  \u05de\u05e9\u05d9\u05de\u05d5\u05ea \u05de\u05d0\u05d5\u05d9\u05e9\u05d5\u05ea: {covered}/{total}",
        'commander_reconcile_uncovered': "\n  \u26a0\ufe0f \u05dc\u05d0 \u05de\u05d0\u05d5\u05d9\u05e9: {tasks}",

        # Notification settings
        'notification_settings': (
            "\u05d4\u05d2\u05d3\u05e8\u05d5\u05ea \u05d4\u05ea\u05e8\u05d0\u05d5\u05ea:\n\n"
            "  1. \u05d3\u05d9\u05d5\u05d5\u05d7\u05d9 \u05d7\u05d9\u05d9\u05dc\u05d9\u05dd/\u05ea\u05e7\u05dc\u05d5\u05ea: {reports}\n"
            "  2. \u05e9\u05d9\u05e0\u05d5\u05d9\u05d9 \u05e6\u05d9\u05d5\u05d3: {gear}\n\n"
            "\u05e9\u05d9\u05e0\u05d5\u05d9\u05d9 \u05dc\u05d5\u05d7 \u05d6\u05de\u05e0\u05d9\u05dd \u05d5\u05d4\u05ea\u05e8\u05d0\u05d5\u05ea \u05de\u05e9\u05d9\u05de\u05d5\u05ea \u05dc\u05d0 \u05de\u05d0\u05d5\u05d9\u05e9\u05d5\u05ea \u05ea\u05de\u05d9\u05d3 \u05e4\u05e2\u05d9\u05dc\u05d5\u05ea.\n\n"
            "  0. \u05d7\u05d6\u05e8\u05d4 \u05dc\u05ea\u05e4\u05e8\u05d9\u05d8"
        ),
        'notif_on': "\u05e4\u05e2\u05d9\u05dc",
        'notif_off': "\u05db\u05d1\u05d5\u05d9",
        'reconcile_uncovered_alert': "\u26a0\ufe0f \u05de\u05e9\u05d9\u05de\u05d5\u05ea \u05dc\u05d0 \u05de\u05d0\u05d5\u05d9\u05e9\u05d5\u05ea: {tasks}",

        # Ordinal suffixes (not used in Hebrew, just number)
        'ordinal_1': '{n}',
        'ordinal_2': '{n}',
        'ordinal_3': '{n}',
        'ordinal_n': '{n}',
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Get a translated string, with optional format kwargs."""
    text = TEXTS.get(lang, TEXTS['en']).get(key, TEXTS['en'].get(key, key))
    if kwargs:
        return text.format(**kwargs)
    return text


def ordinal(lang: str, n: int) -> str:
    """Format a number as an ordinal (1st, 2nd, 3rd, 4th...)."""
    if lang == 'he':
        return str(n)
    if n % 100 in (11, 12, 13):
        return TEXTS['en']['ordinal_n'].format(n=n)
    suffix = {1: 'ordinal_1', 2: 'ordinal_2', 3: 'ordinal_3'}.get(n % 10, 'ordinal_n')
    return TEXTS['en'][suffix].format(n=n)
