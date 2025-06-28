# strings.py

strings = {
    'START_TEXT': "Hello {user_firstname}! I am your **Dedicated Members Adding Bot**.\n\n"
                  "I can help you add members to your Telegram groups safely and efficiently. "
                  "My main focus is to protect your accounts from bans while getting the job done.\n\n"
                  "Use the menu below to get started!",
    'HELP_TEXT_FEATURES': "I am a **dedicated Members Adding Bot**. Here's what I can do:\n\n"
                          "1.  **Multiple Account Login:** Add several Telegram accounts to share the member adding load.\n"
                          "2.  **Smart Member Adding:** I add members with intelligent delays and ban prevention mechanisms to keep your accounts safe.\n"
                          "3.  **Task Management:** Create, start, pause, and monitor multiple adding tasks.\n\n"
                          "There is no need to login your own Telegram account. Use `/addaccount` to start adding accounts for member management.",
    'COMMANDS_TEXT': "Here are the commands you can use:\n\n"
                     "/start - Start the bot and see the welcome message.\n"
                     "/settings - Access all member adding settings.\n"
                     "/addaccount - Add a new Telegram account specifically for adding members.\n"
                     "/myaccounts - View your logged-in member adding accounts and their status.\n"
                     "/createtask - Create a new member adding task.\n"
                     "/managetasks - Manage your existing member adding tasks.\n"
                     "/stats - Get bot usage statistics (Owner only).\n"
                     "/broadcast - Send a broadcast message to your main account's contacts (Owner only).\n",
    'FSUB_MESSAGE': "You must join our updates channel to use this bot.",

    'code_invalid': "Invalid OTP. Please try again.",
    'pass_invalid': "Invalid password. Please try again.",
    
    'wrong_phone': "The phone number provided is not valid or associated with the current login process. Please try again.",
    'msg_404': "Message not found or inaccessible.",
    'user_cooldown': "Please wait {seconds} seconds before trying again.", 

    # New strings for member adding accounts
    'ADD_ACCOUNT_NUMBER_PROMPT': "Please send the **phone number(s)** (with country code, e.g., `+919876543210`) of the account(s) you want to add for member management.\n\n"
                                 "You can send **multiple numbers, one per line**, to add accounts in bulk.",
    'share_phone_number_button': "Share Phone Number", # Not used anymore
    'ADD_ANOTHER_ACCOUNT_PROMPT': "✅ Account login successful! Would you like to add another account for member management?",
    'ASK_OTP_PROMPT': "Please enter the **OTP** (e.g., `123 45`) you received on that Telegram account. *The bot will automatically remove any spaces.*", # Updated OTP instruction
    'ASK_PASSWORD_PROMPT': "Please enter the **2FA password** for that Telegram account.",
    'ACCOUNT_ALREADY_ADDED': "Account with phone number `{phone_number}` is already added.",

    'ACCOUNT_ADDED_SUCCESS': "✅ Account {phone_number} added successfully! Account ID: `{account_id}`",
    'ACCOUNT_LOGIN_FAILED': "Login failed for this account. Reason: {error_message}. Please try again or check your credentials.",
    'NO_ACCOUNTS_FOR_ADDING': "You have no member adding accounts logged in yet. Use '➕ Add Account' first.",
    'MY_ACCOUNTS_HEADER': "📊 **Your Member Adding Accounts**\n\n",
    'ACCOUNT_STATUS_ENTRY': "• Account: `{phone_number}` | ID: `{account_id}`\n"
                            "  Status: {status}\n"
                            "  Daily Adds: {daily_adds}/{limit}\n"
                            "  Soft Errors: {soft_errors}/{soft_limit}\n",
    'ACCOUNT_STATUS_HEALTHY': "✅ Healthy",
    'ACCOUNT_STATUS_SUSPENDED': "⛔ **Suspended** (Reason: {reason})",
    'ACCOUNT_STATUS_FLOODED': "⏳ **Flood Wait** (Until: {until_time})",
    'ACCOUNT_STATUS_INVALID': "❌ **Invalid Session**",
    'ACCOUNT_STATUS_INACTIVE': "⏸️ Inactive",
    'NUMPAD_CONFIRM_BUTTON': "Confirm ✅", # Not used with direct input now
    'BUTTON_ADD_ACCOUNT': "➕ Add Account", # CRITICAL FIX: Added missing string
    'BUTTON_ADD_MORE_ACCOUNT': "➕ Add More Account", # CRITICAL FIX: Added missing string

    'SETTINGS_MENU_TEXT': "⚙️ **Bot Settings**\n\nThis is your main control panel.",
    'CREATE_TASK_PROMPT': "To create a new adding task, please select a **source chat** by its ID or username.",
    'ASK_SOURCE_CHAT_ID': "Please send the **ID or username** of the chat from which to scrape members (e.g., `-1001234567890` for a channel, or `some_channel_username`).\n\n"
                          "You can send **multiple source chats (up to 5), one ID/username per line**.", # New instruction for source chat input
    'ASK_TARGET_CHAT_ID': "Please send the **ID or username** of the **single target chat** where members will be added (e.g., `-1001234567890` for a group, or `some_group_username`).", # New instruction for target chat input
    'TOO_MANY_SOURCE_CHATS': "You can provide a maximum of 5 source chats.",
    'INVALID_CHAT_ID_FORMAT': "Invalid chat ID or username format: `{chat_input}`. Please ensure it's a number (for ID) or a valid username.",
    'CHAT_NOT_FOUND_OR_ACCESSIBLE': "Chat `{chat_input}` not found or not accessible by bot owner.",
    'TASK_SOURCE_SET': "✅ Source chat(s) for task {task_id} set.",
    'TASK_TARGET_SET': "✅ Target chat for task {task_id} set.",
    'TASK_NO_SOURCE_SELECTED': "Please set source chat(s) first.",
    'TASK_NO_TARGET_SELECTED': "Please set the target chat first.",
    'TASK_NO_ACCOUNTS_ASSIGNED': "Please assign at least one active account to this task.",
    'TASK_CREATED_SUCCESS': "✅ Adding task created successfully!",
    'MANAGE_TASKS_HEADER': "⚙️ **Your Configured Adding Tasks**\n\n",
    'TASK_ENTRY_INFO': "• Task {task_id} ({status})\n  Source: {source_chat_title}\n  Targets: {target_chat_titles}\n  Accounts: {num_accounts} assigned",
    'TASK_STATUS_ACTIVE': "Active ▶️",
    'TASK_STATUS_PAUSED': "Paused ⏸️",
    'TASK_STATUS_DRAFT': "Draft 📝",
    'TASK_STATUS_COMPLETED': "Completed ✅",

    'SELECT_SOURCE_CHAT': "Select the chat from which to scrape members for Task {task_id}:", # Original menu style, might be obsolete
    'SELECT_TARGET_CHAT': "Select the chat(s) where members will be added for Task {task_id}:", # Original menu style, might be obsolete
    
    # --- FIX: Improved interface text ---
    'TASK_DETAILS_HEADER': "⚙️ **Task #{task_id} Configuration**\n\n"
                           "**Status:** {status}\n"
                           "**Total Added:** {total_added} members\n\n"
                           "**Source Chat(s):**\n{source_chat_info}\n\n"
                           "**Target Chat:**\n{target_chat_info}\n\n"
                           "**Assigned Accounts:**\n{assigned_accounts_info}",
    # --- END FIX ---

    'TASK_STARTING': "Starting adding task {task_id}...",
    'TASK_PAUSING': "Pausing adding task {task_id}...",
    'TASK_STOPPING': "Stopping adding task {task_id}...",
    'TASK_DELETE_CONFIRM': "Are you sure you want to delete Task {task_id}? This action cannot be undone.",
    'TASK_DELETED': "✅ Task {task_id} deleted successfully.",
    'TASK_COMPLETED': "Task {task_id} completed! All members processed.",
    'TASK_PROGRESS': "Running Task {task_id}: {added_count}/{total_members} added ({progress:.1f}%) using Account {account_id}.",
    'ADDING_LIMIT_REACHED': "Account `{account_id}` reached its daily adding limit ({limit} members) or encountered too many errors. Suspending for today.",
    'NO_ACTIVE_ACCOUNTS_FOR_TASK': "No active or available accounts to continue Task {task_id}. Pausing task.",
    'SCRAPING_MEMBERS': "Scraping members from source chat...",
    'SCRAPING_COMPLETE': "Scraping complete! Found {count} members.",
    'ADD_SUCCESS': "Added {count} members to {target_chat_title} using account `{account_id}`.",
    'ADD_FAIL': "Failed to add member to {target_chat_title} using account `{account_id}`: {error_message}.",
    'MEMBER_ALREADY_IN_GROUP': "Member is already in the target group. Skipping.",
    'MEMBER_PRIVACY_RESTRICTED': "Member's privacy settings prevent adding. Skipping.",
    'PEER_FLOOD_DETECTED': "⚠️ **Peer Flood Detected!** Account `{account_id}` has hit a Peer Flood error. Suspending this account for the day to prevent a ban. Please check account activities.",
    'FLOOD_WAIT_DETECTED': "⏳ **Flood Wait** Account `{account_id}` is in flood wait for {seconds} seconds. Waiting...",
    'USER_ACCOUNT_DISCONNECTED': "❌ **User account `{account_id}` disconnected or session invalid.** Please re-login.",
    'OWNER_ACCOUNT_LOGIN_REQUIRED': "You do not need to login your main account. Please use /addaccount to manage other accounts.",
    'CHOOSE_ACCOUNTS_FOR_TASK': "Select accounts to assign to Task {task_id}. Selected accounts will participate in adding members for this task.",
    'ACCOUNT_ASSIGNED_TO_TASK': "✅ Account `{account_id}` assigned to Task {task_id}.",
    'ACCOUNT_UNASSIGNED_FROM_TASK': "☑️ Account `{account_id}` unassigned from Task {task_id}.",
    'NO_ACCOUNTS_SELECTED_FOR_TASK': "Please select at least one account for this task.",
    'ACCOUNTS_UPDATED_FOR_TASK': "✅ Accounts updated for Task {task_id}.",
    'QUEUE_FULL_MESSAGE': "⚠️ Your bot owner command queue is full. Please wait.",
    'QUEUE_MESSAGE': "✅ Your request has been added to the queue. There are {queue_size} items ahead of you.",
    'BROADCAST_STARTED': "Starting broadcast to your main contacts...",
    'BROADCAST_PROGRESS': "Broadcasting... Sent to: {sent_count}/{total_count}, Failed: {failed_count}",
    'BROADCAST_PEER_FLOOD': "⚠️ Peer Flood Error encountered during broadcast. Stopping to prevent account issues.",
    'BROADCAST_COMPLETE': "**Broadcast Complete**\n\n- **Sent to:** `{sent_count}` users\n- **Failed for:** `{failed_count}` users",
    'BROADCAST_MENU_TEXT': "Send me the message you want to broadcast to all your main account's contacts. It can be text or media.",
    'BROADCAST_CANCELLED': "Broadcast cancelled.",
    'TUTORIAL_PROMPT_OWNER': "Please send me the tutorial video. This video will be shown to users when they click 'Tutorial'.",
    'TUTORIAL_SET_SUCCESS': "✅ Tutorial video set successfully!",
    'TUTORIAL_NOT_SET_MSG': "The tutorial video has not been set by the owner yet.",
    'TUTORIAL_REMOVED_SUCCESS': "✅ Tutorial video removed successfully!",
    'USER_BANNED_MESSAGE': "🚫 You are banned from using this bot's features.",
    'BAN_NOTIFICATION_MESSAGE': "You have been banned from using this bot.",
}
