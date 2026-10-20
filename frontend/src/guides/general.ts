import type { GuideDocument } from "./types";
import { guideScreenshots } from "./screenshots";

export const generalGuide: GuideDocument = {
  title: "General Usage Guide",
  audience: "Website & core Discord bot · Available to everyone",
  intro:
    "A complete walkthrough for clan members and siege planners. Learn where to find a plan, how to prepare one, and how to share it through Discord. No technical experience needed.",
  sections: [
    {
      id: "start",
      title: "1. Get started & find your way",
      summary:
        "Sign in, understand the menus, and follow a siege from planning to Discord.",
      tasks: [
        {
          title: "Sign in to your clan’s website",
          steps: [
            "Open the website address provided by your clan. Choose **Sign in**, then **Sign in with Discord**.",
            "Use the Discord account that belongs to your clan’s server. A server is the clan’s shared space in Discord. Review Discord’s request and approve it to return to the website.",
            "If access is refused, ask your clan’s website manager to check your server membership, access role, and member record. Website access may be limited to officers or siege planners.",
          ],
          result:
            "The **Sieges** page appears after sign-in. Anyone can read this guide; opening clan records requires access.",
        },
        {
          title: "Understand the two sets of menus",
          paragraphs: [
            "The **top menu** applies to the whole clan: **Sieges** lists siege events; **Members** holds the main roster; **Posts** holds shared post priorities and the condition reference; **System** shows version information for support. **Guide** brings you back here.",
            "Inside one siege, a second menu has **Board**, **Posts**, **Members**, **Compare**, and **Settings**. These apply to the siege you opened. Check the date in the page heading before making a change.",
            "For example, **Members** at the top edits a clanmate’s main record. **Members** inside a siege edits their participation, attack day, and reserve status for that siege.",
          ],
        },
        {
          title: "A complete planning session",
          steps: [
            "Check the main **Members** roster and personal post preferences.",
            "Create a siege or clone a previous one, then check its date and buildings.",
            "Review the siege’s participating members, reserve sets, and attack days.",
            "Enter this siege’s post conditions; review building and post assignments.",
            "Validate the plan, compare it with the previous siege, then share images and assignment messages through Discord.",
            "Use **Start Siege** when ready. After the event, use **Close Siege** to preserve its record.",
          ],
          note: "These instructions explain what the controls do. Follow your clan’s decisions about who may edit and when to publish. Use a computer for planning; the board needs a larger screen.",
        },
        {
          title: "Know when a change is saved",
          paragraphs: [
            "Some controls save immediately: dropping a name onto a position, changing a building level, changing an attack day or checkbox, choosing a shared post priority, and assigning a post member.",
            "Other screens have an explicit button: **Save** for member details, **Save Preferences** for personal preferences, **Save Settings** for the siege date, and **Save Conditions** for a post’s restrictions.",
            "An auto-fill or suggestion **preview** is a proposal. It changes the plan only after **Apply**. **Cancel** or closing an unapplied preview leaves those proposed changes unused. Check for an error or success message before leaving a screen.",
          ],
        },
      ],
    },
    {
      id: "read-plan",
      images: guideScreenshots["read-plan"],
      title: "2. Find & read your assignment",
      summary:
        "Find the right date, highlight a member, and distinguish placements, reserves, and attack days.",
      tasks: [
        {
          title: "Find the current siege",
          location: "Top menu → Sieges",
          steps: [
            "Find the date your officers are working on. Check its status: **Planning** is being prepared, **Active** is underway, and **Complete** is a finished record.",
            "Choose **Board**, **Posts**, or **Members** on that siege’s row to jump straight to that view. Clicking the date’s row opens its settings.",
            "If you do not have website access, read the latest assignment images or personal message shared in your clan’s Discord channels. A channel is one conversation inside the server.",
          ],
        },
        {
          title: "Read building assignments",
          location: "Open a siege → Board → Buildings",
          steps: [
            "Use **Search...** in the member list on the left to find a name. If the list is filtered by strength role, choose **All** to see everyone.",
            "Click the member’s name once to highlight their positions. Click it again to clear the highlight. This selects a person to inspect; it does not change their assignments.",
            "Read each highlighted building’s type and number, then the group and position number. For example, **Mana Shrine 1, Group 2, Position 3** identifies one placement inside that building.",
            "Switch to the board’s **Posts** view to check post placements as well. Note the active condition and matched condition shown for each post.",
          ],
          note: "A building assignment tells you where to place a defense team. An **Attack Day** tells you when to attack. They are different parts of the plan.",
        },
        {
          title: "Understand empty, reserve, and disabled positions",
          paragraphs: [
            "A dash or empty slot has no member assigned. **RESERVE** is a deliberate placeholder for a backup placement, not a named member’s assignment. **DISABLED** means that position cannot be used in the current building layout.",
            "The board’s summary counts total, assigned, reserve, and empty positions. Use it to spot unfinished planning, then inspect the positions themselves.",
            "If your name is missing, your team cannot meet a condition, or a reserve placement needs filling, ask the coordinating officer before placing a different team.",
          ],
        },
        {
          title: "Find your attack day and reserve-set status",
          location: "Open a siege → Members",
          steps: [
            "Find your name in the siege’s member table.",
            "Read **Attack Day**: Day 1 or Day 2. A dash means no day has been assigned.",
            "Read **Reserve Set** to see the recorded reserve-set status. Ask an officer what your clan expects you to prepare.",
          ],
          note: "**Override (Pinned)** protects a manually chosen attack day during automatic day assignment. It does not pin a defense position on the board.",
        },
      ],
    },
    {
      id: "roster",
      images: guideScreenshots["roster"],
      title: "3. Manage the clan roster",
      summary:
        "Add and edit members, record power and strength roles, connect Discord accounts, and handle inactive members.",
      tasks: [
        {
          title: "Add or edit a clanmate",
          location: "Top menu → Members",
          steps: [
            "Choose **Add Member** for someone new, or click an existing member’s row to edit them.",
            "Enter their **Name** and **Discord Username**. Use their actual Discord username, not a server nickname, when entering it manually.",
            "Choose their **Role**: Heavy Hitter, Advanced, Medium, or Novice. These are planning strength categories; they do not give Discord permissions or website officer access.",
            "Choose the appropriate **Power Level** range, then choose **Save**. Check the result before leaving.",
          ],
          note: "Adding someone to the main roster does not mean they have been added to every existing siege. Check that siege’s own Members tab separately.",
        },
        {
          title: "Filter the roster and find inactive members",
          steps: [
            "Use the role filter to show a strength category or choose **All Roles**.",
            "Keep **Active only** ticked for the current roster. Untick it to include inactive records.",
            "If a member seems to be missing, clear both filters before adding a duplicate record.",
          ],
        },
        {
          title: "Match website records with Discord accounts",
          location: "Top menu → Members → Sync Discord",
          steps: [
            "Choose **Fetch Preview** to get proposed account matches.",
            "Read the current and proposed usernames and the match label for each row. **Exact** is the strongest match; **Suggested** needs checking; **Ambiguous** needs your judgment.",
            "Review even preselected rows. Untick any match you cannot verify. An ambiguous or unmatched person may need their website record corrected first.",
            "Choose **Apply Selected** when you are satisfied. Only checked rows are applied. Close the preview without applying if the matches are wrong.",
          ],
          result:
            "The selected website records are connected to the proposed Discord accounts. Correct matching helps sign-in, messages, and supported Discord features identify the right person.",
        },
        {
          title: "Deactivate a former member",
          location: "Top menu → Members → Member’s name",
          steps: [
            "Open the correct record and choose **Deactivate**.",
            "Read and confirm the dialog only after checking the person’s identity.",
            "Check any siege still in planning for leftover placements or participation that need updating. Do not assume that an older siege’s history has been rewritten.",
          ],
          result:
            "The person is marked inactive and disappears from the default active-only roster. Their record remains available when you untick Active only.",
        },
      ],
    },
    {
      id: "preferences",
      images: guideScreenshots["preferences"],
      title: "4. Record personal post preferences",
      summary:
        "Tell the planner which restrictions suit a member’s teams. Preferences help suggestions; they do not assign positions.",
      tasks: [
        {
          title: "Choose a member’s preferences",
          location: "Top menu → Members → Member’s name → Post Preferences",
          steps: [
            "Read the conditions and tick the ones that the member’s teams can handle. A condition is a rule restricting which champions or teams can be used at a post.",
            "Use **Filter conditions...** to narrow the list by description. Switch **Group by: Level / Type** to browse by condition level or by categories such as affinity, faction, and rarity.",
            "Review your choices, then choose **Save Preferences**. Look for **Preferences saved.** before leaving.",
            "Reopen the member’s page if you want to verify what was stored.",
          ],
          note: "Preferences belong to the member’s main record. They help planners choose post placements, but saving them does not automatically move an existing assignment. Save member details and preferences with their separate buttons.",
        },
      ],
    },
    {
      id: "create",
      images: guideScreenshots["create"],
      title: "5. Create or clone a siege",
      summary:
        "Start a blank event or reuse a previous layout, then check everything that must change for the new date.",
      tasks: [
        {
          title: "Create a blank siege",
          location: "Top menu → Sieges → New Siege",
          steps: [
            "Leave **Clone from existing siege (optional)** set to **No — create blank siege**.",
            "Check the suggested **Date** and change it if necessary. A suggestion is only a starting point; confirm the actual event date with the clan.",
            "Choose **Create Siege**. The new siege opens in Planning with the active roster added as participants and its starting building layout ready for review.",
            "Check buildings, participating members, attack days, conditions, and placements before sharing it.",
          ],
        },
        {
          title: "Clone a previous siege",
          steps: [
            "In **New Siege**, select an existing siege from the clone dropdown and choose **Clone Siege**. You can also open an existing siege’s **Settings**, choose **Clone**, and confirm.",
            "The copy is a new **Planning** siege. Open **Settings**, enter the new date, and choose **Save Settings**.",
            "Review the copied building levels, broken flags, active member placements, reserve positions, and post priority descriptions.",
            "Enter the new siege’s post conditions. These are cleared on a clone because the game assigns fresh conditions for each siege.",
            "Recheck participating members and assign attack days for the new event. Inactive members’ old placements are cleared rather than carried forward.",
          ],
          note: "Cloning gives you a head start, not a finished plan. The new siege has no date until you save one, and it should be reviewed before any Discord messages are sent.",
        },
      ],
    },
    {
      id: "buildings",
      images: guideScreenshots["buildings"],
      title: "6. Set the date & building layout",
      summary:
        "Match the game’s building levels and broken buildings before assigning teams.",
      tasks: [
        {
          title: "Set or correct the siege date",
          location: "Open a Planning siege → Settings → Siege Settings",
          steps: [
            "Choose the correct date using the **Date** field.",
            "Choose **Save Settings**, then look for **Saved.**.",
            "Check the heading and date again before generating images or sending messages.",
          ],
        },
        {
          title: "Record building levels and damage",
          location: "Open a Planning siege → Settings → Buildings",
          steps: [
            "Find the building by its type and number. Compare it with the current game layout.",
            "Choose the correct **Lvl** number from 1 to 6. The level controls the available team positions.",
            "Tick **Broken** for a broken building, or untick it after repair. These controls save as you change them; they do not wait for Save Settings.",
            "Return to **Board** to inspect the resulting available and disabled positions, then validate the plan again.",
          ],
          note: "Changing a building’s capacity can affect which positions can be used. Finish these checks before filling the board. Existing assignments should be reviewed after any layout change.",
        },
        {
          title: "Understand Defense Scroll Count",
          paragraphs: [
            "**Defense Scroll Count** is calculated from the buildings and their levels. It is a displayed total, not a number you type into a form.",
            "The displayed per-member limit is **3** when the total is below 90, or **4** when it is 90 or more. Marking a building Broken does not reduce the theoretical total used for this limit.",
            "Use the board’s per-member assignment counts to check how those available placements are being used.",
          ],
        },
      ],
    },
    {
      id: "siege-members",
      images: guideScreenshots["siege-members"],
      title: "7. Set participants, attack days & reserves",
      summary:
        "Manage membership for one siege, protect manual attack days, and record reserve sets.",
      tasks: [
        {
          title: "Add or remove a participant",
          location: "Open a Planning siege → Members",
          steps: [
            "Choose **Add Member**, select an active clanmate not already in this siege, and choose **Add**.",
            "If they are unavailable in the picker, check their main roster record and whether they are already participating.",
            "To remove someone from this siege, choose the remove button on their row and read the confirmation. Confirm only when their placements should also be cleared.",
          ],
          note: "Removing a participant clears their position assignments, attack day, and reserve state for this siege. It does not deactivate their main clan record.",
        },
        {
          title: "Choose and pin an attack day",
          steps: [
            "On a member’s row, choose **Day 1** or **Day 2** from **Attack Day**. A dash clears the assigned day. The change saves immediately.",
            "Tick **Override (Pinned)** if automatic day assignment must preserve that member’s chosen day. Leave it unticked if the automatic process may choose their day.",
            "Run **Auto-Assign Attack Days** to see a **preview**. Read the Day 1 and Day 2 lists, including the pinned choices.",
            "Choose **Apply** to use the preview or **Cancel** to leave it unapplied. Check the member table afterward.",
          ],
          note: "Pinned means a protected attack-day choice. It does not protect a building placement from every editing control.",
        },
        {
          title: "Record reserve sets",
          steps: [
            "Use a member’s **Reserve Set** checkbox to record their status manually. The checkbox saves immediately.",
            "Use **Auto-Assign Reserves** if you want the website to set reserve flags automatically. This action applies directly; review the resulting checkboxes afterward.",
            "Tell the members what their reserve responsibilities mean for this siege, especially if the flags changed.",
          ],
          note: "A member’s Reserve Set checkbox and a board position marked RESERVE describe different things. Check both when preparing the backup plan.",
        },
      ],
    },
    {
      id: "board",
      images: guideScreenshots["board"],
      title: "8. Build & edit the assignment board",
      summary:
        "Search members, place teams, replace or clear a placement, and mark reserve positions.",
      tasks: [
        {
          title: "Place or replace a member",
          location: "Open a Planning siege → Board → Buildings",
          steps: [
            "Find the intended member in the left-hand list. Use **Search...** or the strength-role filter if the list is long.",
            "Check the member’s displayed assignment count against the per-member limit shown above the board.",
            "Drag the member’s name from the list and drop it onto the intended active position in the correct building and group. Dropping on an occupied position replaces that position’s member.",
            "Check the name that appears in the position and the updated counts. The placement saves immediately; there is no board-wide Save button.",
            "If moving a placement, inspect the old position too. Adding a placement from the member list does not mean every previous placement for that member has been removed.",
          ],
          note: "Dragging is editing. Clicking a name to highlight its placements is just viewing. Disabled positions cannot accept a placement.",
        },
        {
          title: "Clear a position or leave it for a reserve",
          steps: [
            "Point at an available position to reveal its small down-arrow button, then click it to open the position controls.",
            "Choose **Clear** to remove the member and reserve marker, leaving an empty position.",
            "Choose **Mark RESERVE** to replace the current occupant with a reserve placeholder. This removes the named member from that position.",
            "Check the board after either action. These changes save immediately. To put a named member back, drag their name onto the position again.",
          ],
        },
        {
          title: "Read counts and role labels",
          paragraphs: [
            "The member list shows how many assignments each person has. An over-limit count is a reason to inspect their placements and run validation.",
            "**HH**, **Adv**, **Med**, and **Nov** abbreviate Heavy Hitter, Advanced, Medium, and Novice. They match the strength categories in the main roster.",
            "Use each building’s heading, group, and position numbers when describing an issue to another planner. This avoids confusion when the same person fills several slots.",
          ],
        },
      ],
    },
    {
      id: "autofill",
      images: guideScreenshots["autofill"],
      title: "9. Preview auto-fill & validate a plan",
      summary:
        "Review proposed building assignments, apply them deliberately, and resolve reported problems.",
      tasks: [
        {
          title: "Fill remaining building positions",
          location: "Open a Planning siege → Board → Preview Auto-fill",
          steps: [
            "Check the roster, building layout, and any deliberate placements or reserves first.",
            "Choose **Preview Auto-fill**. Read the proposal by building, group, and position. Check the proposed members and any reserve or unassigned entries.",
            "Choose **Apply** to use the proposal or **Cancel** to leave it unused. Do not treat opening the preview as saving a plan.",
            "Review the board again afterward. Check post assignments separately with the post tools below.",
          ],
        },
        {
          title: "Check the whole plan",
          location:
            "Board → Validate, or Settings → Validation → Run Validation",
          steps: [
            "Run validation after the main edits and again before sharing the plan.",
            "Read each **Error** and **Warning**, including its member or building details. Errors block starting a siege and sending assignment DMs. Warnings need a planner’s review.",
            "Correct the cited issue in the appropriate screen: building levels or broken flags in Settings; participation and days in Members; placements on Board; post restrictions on Posts.",
            "Run validation again. **No issues found.** means the check has no errors or warnings to report; still confirm that the plan matches the clan’s intentions.",
          ],
          note: "If the plan changes after a preview or validation result, review or validate the updated plan. A previous result describes what was checked at that time.",
        },
      ],
    },
    {
      id: "post-conditions",
      images: guideScreenshots["post-conditions"],
      title: "10. Enter each siege’s post conditions",
      summary:
        "Set current restrictions, browse by level or type, and keep conditions distinct from priorities and preferences.",
      tasks: [
        {
          title: "Enter the conditions shown in the game",
          location: "Open a Planning siege → Posts",
          steps: [
            "Find the correct post number and use its expand arrow to open the condition editor.",
            "Read the conditions in the game, then select the matching descriptions on the website. A post can have up to **three** active conditions.",
            "Use **Filter conditions...** to search. Use the page-level **Group by: Level / Type** to change grouping across rows, or a row’s grouping control to browse that post.",
            "Choose **Save Conditions** on that post. Check the condition labels shown on the collapsed row afterward.",
            "Repeat for each post. A cloned siege needs its conditions entered for the new event.",
          ],
          note: "A **post’s active conditions** describe the current game restrictions. A **member’s preferences** describe which restrictions suit their teams. A **post’s priority** describes how important that post is to the plan.",
        },
        {
          title: "Interpret the match label",
          paragraphs: [
            "An assigned post may show a matched-condition label or **No condition match**. Use this to review whether the recorded placement has a suitable preference match.",
            "A saved preference or a suggested match is a planning aid. Confirm with the member that their actual defense team satisfies the game’s requirement.",
          ],
        },
      ],
    },
    {
      id: "post-assignments",
      images: guideScreenshots["post-assignments"],
      title: "11. Assign posts & review suggestions",
      summary:
        "Choose a member and matched condition manually, or review a full set of suggested post placements.",
      tasks: [
        {
          title: "Assign one post manually",
          location: "Open a Planning siege → Board → Posts",
          steps: [
            "Expand the post you want to fill. Read its active conditions and the current assigned member or reserve status.",
            "Review the member choices and their matching preferences. If more than one condition is offered for a member, choose the intended matching condition.",
            "Choose **Assign** beside that member. If a duplicate-condition warning appears, review the other post it names. Use **Confirm anyway** only if repeating the condition is intentional.",
            "Check the member and matched condition shown on the post after it updates. Assign saves immediately and clears that position’s reserve status.",
          ],
          note: "Use **Edit conditions** in a post’s expanded view to open its condition editor. Setting conditions and choosing the assigned member are separate actions.",
        },
        {
          title: "Mark a post as reserve",
          steps: [
            "Expand the post on **Board → Posts** and choose **Mark RESERVE**.",
            "Check that the post now shows the reserve placeholder instead of a named member.",
            "To replace the placeholder later, choose a member and use **Assign**. Recheck their matched condition.",
          ],
        },
        {
          title: "Review suggested post assignments",
          location: "Board → Posts → Suggest Assignments",
          steps: [
            "Make sure the current conditions, member preferences, and priorities are correct before opening suggestions.",
            "Choose **Suggest Assignments**. Read the summary categories: new assignments fill empty posts; replacements change a member; unchanged entries stay as they are; skipped entries explain why no placement was proposed.",
            "Inspect the current member, proposed member, priority, and matched condition for each suggested change. Use the category filters to focus the list.",
            "Untick every change you do not want. New and replacement suggestions may already be selected, so review the full selection count before applying.",
            "Choose **Apply** with the displayed count to use only the selected changes. Close the window to abandon an unapplied proposal.",
          ],
        },
        {
          title: "Handle skipped, expired, or changed suggestions",
          paragraphs: [
            "A post can be skipped because it has no conditions, no matching member, a reserve marker, or a disabled position. Fix the relevant input if it should receive a suggestion.",
            "The preview has a limited lifetime. If it expires, refresh the preview and review the new proposal before applying.",
            "If another planner changed a position while you were reviewing, the website reports that the preview is out of date. Review the affected posts and refresh. **Apply remaining** applies only the still-valid remainder, so read the remainder carefully before using it.",
          ],
        },
      ],
    },
    {
      id: "priorities",
      images: guideScreenshots["priorities"],
      title: "12. Set shared post priorities",
      summary:
        "Maintain the clan’s priority map and descriptive notes, and browse the full condition reference.",
      tasks: [
        {
          title: "Choose priority and description for a post",
          location: "Top menu → Posts → Priorities",
          steps: [
            "Find the post number, then choose **Low**, **Medium**, or **High** from its priority list. This selection saves immediately.",
            "Enter a useful location description, such as “Near Mana Shrine 1”. Click or tab out of the description field to save the edited text.",
            "Check the values before leaving and review them when preparing the next siege.",
          ],
          note: "This top-menu page is shared planning configuration. It is separate from a siege’s Posts tab, where you enter that siege’s conditions. Recheck the siege’s displayed priorities when creating or cloning a plan.",
        },
        {
          title: "Browse all available conditions",
          location: "Top menu → Posts → Conditions",
          steps: [
            "Choose **Conditions** to see the reference list.",
            "Switch **Group by: Level / Type** to browse the descriptions in the way you find easiest.",
            "Use the reference to understand a condition before recording a member’s preference or choosing a post’s current restrictions.",
          ],
        },
      ],
    },
    {
      id: "compare",
      images: guideScreenshots["compare"],
      title: "13. Compare with a previous siege",
      summary:
        "See who gained, lost, or kept placements before explaining changes to the clan.",
      tasks: [
        {
          title: "Compare assignments side by side",
          location: "Open a siege → Compare",
          steps: [
            "Leave **Compare against** set to **Most recent completed**, or choose another completed siege from the dropdown.",
            "Read **Old Positions** and **New Positions** for each member. Added placements are green, removed placements are red, and unchanged placements are gray.",
            "Tick **Show diffs only** to show only members whose positions changed. Untick it to see the full comparison.",
            "Check the summary counts and use the detailed building, group, and position labels when explaining a move.",
          ],
          note: "Comparison is for reviewing placements; it does not apply changes. If it cannot find a previous completed siege, choose an available completed record or return after one has been completed.",
        },
      ],
    },
    {
      id: "discord",
      images: guideScreenshots["discord"],
      title: "14. Share through the core Discord bot",
      summary:
        "Prepare images, post the plan to clan channels, send personal assignment DMs, and check delivery.",
      tasks: [
        {
          title: "Prepare images without sending messages",
          location: "Open a siege → Settings → Discord Notifications",
          steps: [
            "Confirm the siege date and review the assignments first.",
            "Choose **Generate Images**. Review the **Assignments** and **Reserves** previews shown on the page.",
            "Use **Download** under the relevant preview if you need a copy to share yourself.",
          ],
          result:
            "You have previewed or downloaded the images. Generating them does not itself post them to Discord.",
        },
        {
          title: "Post assignment images to clan channels",
          steps: [
            "Validate and review the plan, then choose **Post to Discord** under **Discord Notifications**.",
            "Read the confirmation and send only when the clan should receive this plan.",
            "Read the website’s result and check the configured assignment channel in Discord for the new post. Recheck the date if several versions have been shared.",
          ],
          note: "The destination channels are set for your clan’s website. There is no channel picker in this workflow. Ask the website manager if messages go to the wrong place.",
        },
        {
          title: "Send personal assignment messages",
          steps: [
            "Check the siege date, member Discord accounts, placements, and validation result.",
            "Choose **Notify Members**, then read and confirm the send dialog. This sends assignment DMs to the participating members; it is a real send, not a preview.",
            "Wait while the batch is in progress. The result area shows progress and a result for each member.",
            "Review every failure, missing username, or **Status unknown** entry. A completed batch does not guarantee every person received a message.",
          ],
          note: "Avoid repeatedly sending the whole batch while investigating one failure: members who received the first batch may get duplicate messages. Check the result and coordinate any resend.",
        },
        {
          title: "Help a member who did not receive a DM",
          steps: [
            "Check their saved Discord username and account match on the main roster. Correct a missing or incorrect record before sending again.",
            "Ask them to check Discord message requests, allow direct messages from server members in that server’s privacy settings, and confirm that they have not blocked the bot.",
            "Read the website’s delivery result. If the service reported an error or an unknown result, ask the website manager to investigate before a broad resend.",
          ],
        },
        {
          title: "What members do in Discord",
          paragraphs: [
            "Read the latest assignment image in the clan’s assignment channel, or open the bot’s direct message for your personal placements. Follow the date and instructions agreed by the officers.",
            "The core bot’s website features are used through the website controls described above. Ask a planner to correct an assignment or an account mismatch; replying to an assignment DM does not edit the plan.",
            "Where your clan has enabled siege-day roles, changing attack-day assignments can update the corresponding Discord labels. If a label disagrees with the published plan, ask an officer to check it.",
          ],
        },
      ],
    },
    {
      id: "lifecycle",
      images: guideScreenshots["lifecycle"],
      title: "15. Start, close, reopen & delete",
      summary:
        "Understand the editing lock and preserve the right record for each siege.",
      tasks: [
        {
          title: "Start the planned siege",
          location: "Open a Planning siege → Settings → Lifecycle",
          steps: [
            "Finish the roster, building layout, placements, conditions, and attack-day review. Validate and resolve blocking errors.",
            "Choose **Start Siege** when the clan is ready for the plan to become active.",
            "Check that the status changes to **Active**. If another siege is already active, resolve that event’s status before trying again.",
          ],
          note: "Siege editing is allowed during **Planning**. Both **Active** and **Complete** lock siege changes. Discord sharing is a separate action; changing the lifecycle does not substitute for checking delivery.",
        },
        {
          title: "Close the event and preserve its history",
          steps: [
            "After the active event finishes, open its **Settings** and choose **Close Siege**.",
            "Check that its status becomes **Complete**. Keep the record for comparison and future cloning.",
          ],
        },
        {
          title: "Reopen a completed record for a correction",
          steps: [
            "Agree on the correction with the coordinating officer first.",
            "Open the completed siege’s **Settings** and choose **Reopen Siege**. It returns to **Planning**, where you can edit it.",
            "Make the correction, validate it, and coordinate any updated Discord communication. Review the lifecycle afterward rather than leaving an old event in the wrong status.",
          ],
          note: "The current screen offers Reopen Siege for a completed event. It does not provide an Active-to-Planning shortcut.",
        },
        {
          title: "Delete a mistaken planning siege",
          steps: [
            "Check the date and record carefully. Open its **Settings → Lifecycle** and choose **Delete**. This control is for a Planning siege.",
            "Read the confirmation. Deleting permanently removes that siege’s buildings and assignments, so choose **Cancel** if you need to keep the record.",
            "Confirm only for the unwanted record. Use Close Siege to retain the history of a completed event.",
          ],
        },
      ],
    },
    {
      id: "help",
      title: "16. Troubleshooting & getting help",
      summary:
        "Resolve common access, editing, matching, validation, and delivery problems.",
      tasks: [
        {
          title: "I cannot sign in or see a member",
          paragraphs: [
            "Check that Discord is using the account connected to your clan’s website. Ask the website manager to check the required access role and account match.",
            "For a missing roster member, choose All Roles and untick Active only. For someone missing from a siege, check participation in that siege’s Members tab. These are different lists.",
          ],
        },
        {
          title: "A control is disabled or an edit was refused",
          paragraphs: [
            "Check the siege status. Active and Complete records are locked against siege edits; prepare changes in a Planning siege.",
            "If someone else changed or started the siege while your page was open, reload and inspect the current plan before trying again.",
            "If a sharing button is disabled, check the date, lifecycle, current sending progress, and any validation error shown beside it.",
          ],
        },
        {
          title: "A post has no suggested member",
          paragraphs: [
            "Check the post’s active conditions, member preferences, participant list, disabled position, and reserve marker. Save any condition or preference edits before refreshing suggestions.",
            "A member with no matching preference may need their preferences reviewed. If the team is suitable despite a missing match, the planner should verify the placement rather than assuming a suggestion is a guarantee.",
          ],
        },
        {
          title: "Tell the website manager what happened",
          steps: [
            "Include the siege date, screen or tab, and the button or action you used.",
            "Copy the visible error text or share a screenshot that helps locate the problem. Include the member, building, group, and position when relevant.",
            "Use **System** in the top menu to find the displayed component versions if asked. The changelog menu in the header shows what changed in releases.",
          ],
          note: "If an edit failed, do not assume it saved. Reload the relevant record and verify the visible result before telling the clan that the plan changed.",
        },
      ],
    },
  ],
};
