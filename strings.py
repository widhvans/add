# strings.py

strings = {
    'START_TEXT': "Hello {user_firstname}! I am your **Dedicated Members Adding Bot**.\n\n"
                  "I can help you add members to your Telegram groups safely and efficiently. "
                  "My main focus is to protect your accounts from bans while getting the job done.",
    'HELP_TEXT_FEATURES': "I am a **dedicated Members Adding Bot**. Here's what I can do:\n\n"
                          "1.  **Multiple Account Login:** Add several Telegram accounts to share the member adding load.\n"
                          "2.  **Smart Member Adding:** I add members with intelligent delays and ban prevention mechanisms to keep your accounts safe.\n"
                          "3.  **Task Management:** Create, start, pause, and monitor multiple adding tasks.\n\n"
                          "Use /login to get started!",
    'COMMANDS_TEXT': "Here are the commands you can use:\n\n"
                     "/start - Start the bot and see the welcome message.\n"
                     "/help - Get information about my features.\n"
                     "/commands - See a list of available commands.\n"
                     "/login - Log in your **main** Telegram account to use my features.\n"
                     "/logout - Log out from your **main** Telegram account.\n"
                     "/settings - Access all member adding settings.\n"
                     "/addaccount - Add another Telegram account specifically for adding members.\n"
                     "/myaccounts - View your logged-in member adding accounts and their status.\n"
                     "/createtask - Create a new member adding task.\n"
                     "/managetasks - Manage your existing member adding tasks.\n"
                     "/stats - Get bot usage statistics.\n"
                     "/broadcast - Send a broadcast message (Owner only).\n",
    'FSUB_MESSAGE': "You must join our updates channel to use this bot.",
    'need_login': "Please login your **main** Telegram account using /login to access this feature.",
    'session_invalid': "Your session is invalid. Please login again using /login.",
    'already_logged_in': "You are already logged in with your main account. Use /logout to log out if needed.",
    'ask_phone': "Please click the button below to share your phone number for login.",
    'ask_code': "Please enter the OTP you received on your Telegram account.",
    'ask_pass': "Please enter your 2FA password.",
    'code_invalid': "Invalid OTP. Please try again.",
    'pass_invalid': "Invalid password. Please try again.",
    'LOGIN_SUCCESS_TEXT': "Login successful for your main account! You can now use the bot's features.",
    'logout_sure': "Are you sure you want to log out from your main session?",
    'logged_out': "You have been successfully logged out from your main account.",
    'not_logged_out': "Logout cancelled.",
    'wrong_phone': "The phone number you sent is not associated with your Telegram account or the current login process. Please try again with the correct phone number.",
    'msg_404': "Message not found or inaccessible.", # Keep for now, might be removed if no relevant context
    'user_cooldown': "Please wait {seconds} seconds before trying again.", # Keep for now, might be removed if no relevant context

    # Member Adding Specific Strings
    'SETTINGS_MENU_TEXT': "Choose a setting to configure for member adding:",
    'ADD_ACCOUNT_PROMPT': "Please forward your phone number to login to the **member adding account**.",
    'ACCOUNT_ADDED_SUCCESS': "✅ Account {phone_number} added successfully! Account ID: <code>{account_id}</code>",
    'ACCOUNT_LOGIN_FAILED': "Login failed for this account. Reason: {error_message}. Please try again or check your credentials.",
    'NO_ACCOUNTS_FOR_ADDING': "You have no member adding accounts logged in yet. Please use '➕ Add Account' first.",
    'MY_ACCOUNTS_HEADER': "📊 **Your Member Adding Accounts**\n\n",
    'ACCOUNT_STATUS_ENTRY': "• Account: <code>{phone_number}</code> | ID: <code>{account_id}</code>\n"
                            "  Status: {status}\n"
                            "  Daily Adds: {daily_adds}/{limit}\n"
                            "  Soft Errors: {soft_errors}/{soft_limit}\n",
    'ACCOUNT_STATUS_HEALTHY': "✅ Healthy",
    'ACCOUNT_STATUS_SUSPENDED': "⛔ Suspended (Reason: {reason})",
    'ACCOUNT_STATUS_FLOODED': "⏳ Flood Wait (Until: {until_time})",
    'ACCOUNT_STATUS_INVALID': "❌ Invalid Session",
    'ACCOUNT_STATUS_INACTIVE': "⏸️ Inactive",

    'CREATE_TASK_PROMPT': "To create a new adding task, please first select a **source chat** from which to scrape members.",
    'TASK_SOURCE_SET': "✅ Source chat for task {task_id} set to **{chat_title}**. Now select **target chat(s)**.",
    'TASK_TARGET_SET_SINGLE': "✅ Target chat for task {task_id} set to **{chat_title}**. You can add one more or click Done.",
    'TASK_TARGET_SET_MULTI': "✅ Target chat for task {task_id} added: **{chat_title}**.",
    'TASK_TARGET_UNSET': "☑️ Target chat for task {task_id} removed: **{chat_title}**.",
    'TASK_NO_SOURCE_SELECTED': "Please select a source chat first.",
    'TASK_NO_TARGET_SELECTED': "Please select at least one target chat.",
    'TASK_NO_ACCOUNTS_ASSIGNED': "Please assign at least one active account to this task.",
    'TASK_CREATED_SUCCESS': "✅ Adding task created successfully!",
    'MANAGE_TASKS_HEADER': "⚙️ **Manage Your Adding Tasks**\n\n",
    'TASK_ENTRY_INFO': "• Task {task_id} ({status})\n  Source: {source_chat_title}\n  Targets: {target_chat_titles}\n  Accounts: {num_accounts} assigned",
    'TASK_STATUS_ACTIVE': "Active ▶️",
    'TASK_STATUS_PAUSED': "Paused ⏸️",
    'TASK_STATUS_DRAFT': "Draft 📝",
    'TASK_STATUS_COMPLETED': "Completed ✅",

    'SELECT_SOURCE_CHAT': "Select the chat from which to scrape members for Task {task_id}:",
    'SELECT_TARGET_CHAT': "Select the chat(s) where members will be added for Task {task_id}:",
    'TASK_DETAILS_HEADER': "📝 **Task {task_id} Details**\n\nStatus: {status}\nSource: {source_chat_info}\nTargets: {target_chat_info}\nAssigned Accounts: {assigned_accounts_info}\nTotal Members Added: {total_added}",
    'TASK_STARTING': "Starting adding task {task_id}...",
    'TASK_PAUSING': "Pausing adding task {task_id}...",
    'TASK_STOPPING': "Stopping adding task {task_id}...",
    'TASK_DELETE_CONFIRM': "Are you sure you want to delete Task {task_id}? This action cannot be undone.",
    'TASK_DELETED': "✅ Task {task_id} deleted successfully.",
    'TASK_COMPLETED': "Task {task_id} completed! All members processed.",
    'TASK_PROGRESS': "Running Task {task_id}: {added_count}/{total_members} added ({progress:.1f}%) using Account {account_id}.",
    'ADDING_LIMIT_REACHED': "Account <code>{account_id}</code> reached its daily adding limit ({limit} members) or encountered too many errors. Suspending for today.",
    'NO_ACTIVE_ACCOUNTS_FOR_TASK': "No active or available accounts to continue Task {task_id}. Pausing task.",
    'SCRAPING_MEMBERS': "Scraping members from source chat...",
    'SCRAPING_COMPLETE': "Scraping complete! Found {count} members.",
    'ADD_SUCCESS': "Added {count} members to {target_chat_title} using account <code>{account_id}</code>.",
    'ADD_FAIL': "Failed to add member to {target_chat_title} using account <code>{account_id}</code>: {error_message}.",
    'MEMBER_ALREADY_IN_GROUP': "Member is already in the target group. Skipping.",
    'MEMBER_PRIVACY_RESTRICTED': "Member's privacy settings prevent adding. Skipping.",
    'PEER_FLOOD_DETECTED': "⚠️ **Peer Flood Detected!** Account <code>{account_id}</code> has hit a Peer Flood error. Suspending this account for the day to prevent a ban. Please check account activities.",
    'FLOOD_WAIT_DETECTED': "⏳ Account <code>{account_id}</code> is in flood wait for {seconds} seconds. Waiting...",
    'USER_ACCOUNT_DISCONNECTED': "❌ User account <code>{account_id}</code> disconnected or session invalid. Please re-login.",
    'OWNER_ACCOUNT_LOGIN_REQUIRED': "Please login your main account first to access this feature.",
    'CHOOSE_ACCOUNTS_FOR_TASK': "Select accounts to assign to Task {task_id}. Selected accounts will participate in adding members for this task.",
    'ACCOUNT_ASSIGNED_TO_TASK': "✅ Account <code>{account_id}</code> assigned to Task {task_id}.",
    'ACCOUNT_UNASSIGNED_FROM_TASK': "☑️ Account <code>{account_id}</code> unassigned from Task {task_id}.",
    'NO_ACCOUNTS_SELECTED_FOR_TASK': "Please select at least one account for this task.",
    'ACCOUNTS_UPDATED_FOR_TASK': "✅ Accounts updated for Task {task_id}.",
    'QUEUE_FULL_MESSAGE': "⚠️ Your bot owner command queue is full. Please wait.",
    'QUEUE_MESSAGE': "✅ Your request has been added to the queue. There are {queue_size} items ahead of you.",
    'BROADCAST_STARTED': "Starting broadcast to your main contacts...",
    'BROADCAST_PROGRESS': "Broadcasting... Sent to: {sent_count}/{total_count}, Failed: {failed_count}",
    'BROADCAST_PEER_FLOOD': "⚠️ Peer Flood Error encountered during broadcast. Stopping to prevent account issues.",
    'BROADCAST_COMPLETE': "<b>Broadcast Complete</b>\n\n- <b>Sent to:</b> <code>{sent_count}</code> users\n- <b>Failed for:</b> <code>{failed_count}</code> users",
    'BROADCAST_MENU_TEXT': "Send me the message you want to broadcast to all your main account's contacts. It can be text or media.",
    'BROADCAST_CANCELLED': "Broadcast cancelled.",
    'TUTORIAL_PROMPT_OWNER': "Please send me the tutorial video. This video will be shown to users when they click 'Tutorial'.",
    'TUTORIAL_SET_SUCCESS': "✅ Tutorial video set successfully!",
    'TUTORIAL_NOT_SET_MSG': "The tutorial video has not been set by the owner yet.",
    'TUTORIAL_REMOVED_SUCCESS': "✅ Tutorial video removed successfully!",
    'USER_BANNED_MESSAGE': "🚫 You are banned from using this bot's features.",
    'BAN_NOTIFICATION_MESSAGE': "You have been banned from using this bot.",
}
