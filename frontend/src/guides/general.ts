import type { GuideDocument } from "./types";
import { guideScreenshots } from "./screenshots";
import { buildingSlotTables } from "./buildingSlots";

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
          images: guideScreenshots["navigation"],
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
            "After deactivation, review any **Planning** siege: the member is removed from its participant list and their placements are cleared, leaving positions to refill.",
          ],
          result:
            "The person is marked inactive and disappears from the default active-only roster. Their record remains available when you untick Active only.",
          note: "Participation and placements in **Active** and **Complete** sieges are preserved. Deactivation is blocked while the member has an assignment in an **Active** siege.",
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
            "You can clone in either of two places: in **New Siege**, select a previous siege from the clone dropdown and choose **Clone Siege**; or open the siege you want to copy, go to **Settings → Lifecycle**, choose **Clone**, and confirm.",
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
            "Choose the correct **Lvl** number from 1 to 6. The level controls the available team positions; see the [building level slot chart](#building-slots) below for the slots in each group.",
            "Tick **Broken** for a broken building: the system uses its **Level 1 slot layout**. Its saved level stays recorded; unticking Broken after repair restores the slot layout for that level. These controls save immediately.",
            "Return to **Board** to inspect the resulting available and disabled positions, then validate the plan again.",
          ],
          note: "Lowering a building’s level removes any groups or slots that no longer fit, including the members assigned to those removed slots. Marking Broken also removes slots beyond the Level 1 layout. Restoring the level or repairing the building recreates those slots empty; their old assignments are not restored. Review the board after either change.",
        },
        {
          id: "building-slots",
          title: "Building level slot chart",
          paragraphs: [
            "Each number is the number of team slots in that group. One slot holds one member’s defense team. A dash (—) means that group does not exist at that level. Total slots is the capacity of one building, not all buildings of that type combined.",
            "For example, a Level 2 Mana Shrine has 3 slots in Group 1, 3 in Group 2, and 1 in Group 3: 7 slots in total. On a small screen, scroll a table sideways to see every group.",
            "Use the Level 1 row when a building is marked Broken. Posts always have one group with one slot.",
          ],
          tables: buildingSlotTables,
        },
        {
          title: "Understand Defense Scroll Count",
          paragraphs: [
            "**Defense Scroll Count** is calculated from the buildings and their levels. It is a displayed total, not a number you type into a form.",
            "The displayed per-member limit is **3** when the total is below 90, or **4** when it is 90 or more. Marking a building Broken does not reduce the theoretical total used for this limit.",
            "Use the board’s per-member assignment counts to check how those available placements are being used.",
          ],
          note: "This calculation sets the **enforced defense-scroll limit per member**: 3 or 4 assignments. If any member has more than this limit, the website blocks **Notify Members** from sending assignment messages. Reduce or reassign their extra placements, then run **Validate** again before sending.",
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
          title: "Assign and pin attack days",
          location: "Open a Planning siege → Members",
          steps: [
            "On a member’s row, choose **Day 1** or **Day 2** from **Attack Day**. A dash clears the assigned day. The change saves immediately.",
            "Tick **Override (Pinned)** if automatic day assignment must preserve that member’s chosen day. Leave it unticked if the automatic process may choose their day.",
            "Run **Auto-Assign Attack Days** to see a **preview**. Read the Day 1 and Day 2 lists, including the pinned choices.",
            "Choose **Apply** to use the preview or **Cancel** to leave it unapplied. Check the member table afterward.",
          ],
          resultTitle: "Included in the Discord assignment message",
          result:
            "When you use **Notify Members**, the bot’s personal Discord message includes the member’s **Attack Day**.",
          note: "Pinned means a protected attack-day choice. It does not protect a building placement from every editing control.",
        },
        {
          id: "automatic-attack-days",
          title: "How automatic attack days are chosen",
          paragraphs: [
            "The automatic assignment aims for at least **10 Day 2 members**. Follow the chart from top to bottom; pinned choices take priority over the automatic rules.",
          ],
          tables: [
            {
              caption: "Automatic attack-day decision chart",
              columns: ["Step", "Check", "What happens"],
              rows: [
                [
                  "1",
                  "Is the member pinned?",
                  "Keep their chosen day. Members pinned to Day 2 count toward the target of 10.",
                ],
                [
                  "2",
                  "Unpinned Heavy Hitter or Advanced?",
                  "Assign them to Day 2, even if this takes Day 2 above 10 members.",
                ],
                [
                  "3",
                  "Still fewer than 10 on Day 2?",
                  "Add unpinned Medium members, highest player power first, until Day 2 reaches 10 or no Medium members remain.",
                ],
                [
                  "4",
                  "Still fewer than 10?",
                  "Add unpinned Novices, highest player power first, until Day 2 reaches 10 or no Novices remain.",
                ],
                [
                  "5",
                  "Who is left?",
                  "Assign all remaining unpinned members to Day 1.",
                ],
              ],
            },
          ],
          note: "10 is a minimum target, not a maximum. If too few participants are available after keeping pinned days, Day 2 may still have fewer than 10 members. Review the preview before applying it.",
        },
        {
          title: "Record reserve sets",
          steps: [
            "Use a member’s **Reserve Set** checkbox to record their status manually. The checkbox saves immediately.",
            "Use **Auto-Assign Reserves** if you want the website to set reserve flags automatically. This action applies directly; review the resulting checkboxes afterward.",
            "Tell the members what their reserve responsibilities mean for this siege, especially if the flags changed.",
          ],
          resultTitle: "Included in the Discord assignment message",
          result:
            "When you use **Notify Members**, the bot’s personal Discord message includes the member’s reserve-set status, shown as **Have Reserve Set**.",
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
          note: "Marking a position **RESERVE** means you intend it to be filled by Raid’s **in-game reserve system**. Raid selects reserve teams randomly from the reserve pool, so marking the slot does **not guarantee it will be filled** or which team will fill it.",
        },
        {
          id: "role-labels",
          title: "Read counts and role labels",
          paragraphs: [
            "The member list shows how many assignments each person has. An over-limit count is a reason to inspect their placements and run validation.",
            "Use each building’s heading, group, and position numbers when describing an issue to another planner. This avoids confusion when the same person fills several slots.",
            "The board’s role colors and abbreviations match the strength categories in the main roster:",
          ],
          tables: [
            {
              caption: "Board role colors and abbreviations",
              columns: ["Role", "Abbreviation", "Color"],
              rows: [
                ["Heavy Hitter", "HH", "Red"],
                ["Advanced", "Adv", "Amber (gold)"],
                ["Medium", "Med", "Green"],
                ["Novice", "Nov", "Blue"],
              ],
              colorSwatches: {
                Red: "bg-red-500",
                "Amber (gold)": "bg-amber-500",
                Green: "bg-green-500",
                Blue: "bg-blue-400",
              },
            },
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
          tables: [
            {
              caption: "Errors, warnings, and finalizing the plan",
              columns: ["Result", "What it means", "Effect on finalizing"],
              rows: [
                [
                  "Error",
                  "A problem that must be fixed, such as a member exceeding their scroll limit or missing an attack day.",
                  "Blocks Start Siege and Notify Members until all errors are fixed.",
                ],
                [
                  "Warning",
                  "Something to review, such as empty positions or fewer than 10 Day 2 attackers.",
                  "Does not block Start Siege or Notify Members. Correct unintended issues; you can proceed if the warning reflects your intended plan.",
                ],
              ],
            },
          ],
          steps: [
            "Run validation after the main edits and again before sharing the plan.",
            "Read each **Error** and **Warning**, including its member or building details. Use the table above to see what must be fixed and what needs review.",
            "Correct the cited issue in the appropriate screen: building levels or broken flags in Settings; participation and days in Members; placements on Board; post restrictions on Posts.",
            "Run validation again. **No issues found.** means the check has no errors or warnings to report; still confirm that the plan matches the clan’s intentions.",
          ],
          note: "Before finalizing, resolve every error and review every warning. Warnings alone do not stop you from starting the siege or sending assignment messages. If you change the plan afterward, run validation again so the result reflects the updated plan.",
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
      images: guideScreenshots["post-assignments"].slice(0, 1),
      title: "Assign posts manually",
      summary:
        "Choose a member and matched condition for one post, or mark a post for a reserve.",
      tasks: [
        {
          title: "Assign one post manually",
          location: "Open a Planning siege → Board → Posts",
          images: guideScreenshots["post-expanded"],
          steps: [
            "Expand the post you want to fill. Read its active conditions and the current assigned member or reserve status.",
            "Review the member choices and their matching preferences. If more than one condition is offered for a member, choose the intended matching condition.",
            "Choose **Assign** beside that member. If a warning appears, follow the **Duplicate condition warning** guidance below.",
            "Check the member and matched condition shown on the post after it updates. Assign saves immediately and clears that position’s reserve status.",
          ],
          callout: {
            title: "Duplicate condition warning",
            paragraphs: [
              "This warns that **the same member is already matched to the same condition at another post**. The amber post number beside a condition identifies that other assignment. Different members can use the same condition without triggering this warning.",
              "Choosing **Assign** for that repeated match shows **Already matched on…** and replaces Assign with **Cancel** and **Confirm anyway**. The new assignment has not been saved yet.",
              "Choose **Cancel** to review the other post, then select a different matching condition or another member. Use **Confirm anyway** only when you intend to repeat that member’s condition at both posts. It saves the new assignment without removing the other one.",
            ],
            images: guideScreenshots["post-duplicate-warning"],
          },
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
      ],
    },
    {
      id: "post-suggestions",
      images: guideScreenshots["post-assignments"].slice(1),
      title: "Automatic post assignments",
      summary:
        "Understand how suggestions are chosen, review the preview, and apply the changes you want.",
      tasks: [
        {
          id: "automatic-post-rules",
          title: "How automatic post assignments are chosen",
          paragraphs: [
            "Follow this chart for each post. Suggestions use the siege’s current priorities, conditions, and participants, together with the members’ saved post preferences.",
          ],
          tables: [
            {
              caption: "Automatic post-assignment decision chart",
              columns: ["Step", "Check", "What happens"],
              rows: [
                [
                  "1",
                  "Which post goes first?",
                  "High priority, then Medium, then Low. Within the same priority, the lower post number goes first.",
                ],
                [
                  "2",
                  "Can this post receive a suggestion?",
                  "Ignore broken buildings. Skip reserve or disabled positions, missing positions, and posts with no current conditions.",
                ],
                [
                  "3",
                  "Who matches its conditions?",
                  "Consider active siege participants with a matching saved preference. The same member-and-condition combination cannot be used twice in this preview.",
                ],
                [
                  "4",
                  "No suitable member available?",
                  "Leave the post without a suggestion and show why it was skipped. Continue to the next post.",
                ],
                [
                  "5",
                  "Which suitable member is chosen?",
                  "Prefer the current occupant if they still qualify. Otherwise, prefer fewer assignments; if tied, choose by name alphabetically.",
                ],
                [
                  "6",
                  "What changes for the next post?",
                  "Record the chosen member and an unused matching condition. Count this suggestion alongside their existing assignments before considering the next post.",
                ],
              ],
            },
          ],
          note: "High priority does not guarantee a match. Strength roles and player power do not rank post suggestions. The preview only changes assignments after you review it and choose Apply.",
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
        "Set shared post priorities and location notes, and browse the condition reference.",
      tasks: [
        {
          title: "What post priority is used for",
          paragraphs: [
            "Choose **Low**, **Medium**, or **High** to set a post’s priority. This affects automatic post assignments and is displayed when you set post conditions and assign members during a siege.",
            "See **Siege Management → Automatic post assignments** for how automatic assignments use priority.",
          ],
        },
        {
          title: "Choose priority and description for a post",
          location: "Top menu → Posts → Priorities",
          steps: [
            "Find the post number, then choose **Low**, **Medium**, or **High** from its priority list. This selection saves immediately.",
            "Enter a useful location description, such as “Near Mana Shrine 1”. Click or tab out of the description field to save the edited text.",
            "Check the values before leaving and review them when preparing the next siege.",
          ],
          note: "Shared priorities are copied into new or cloned sieges. Changes here do not update existing sieges.",
        },
        {
          title: "Browse all available conditions",
          location: "Top menu → Posts → Conditions",
          paragraphs: [
            "This page is **purely for reference**. It lists the available post conditions so you can read their requirements; viewing it does not change a member’s preferences or a siege’s conditions.",
          ],
          images: guideScreenshots["conditions-reference"],
          steps: [
            "Choose **Conditions** to see the reference list.",
            "Switch **Group by: Level / Type** to browse the descriptions in the way you find easiest.",
          ],
          note: "If new conditions need to be added in the future, a **database administrator** must add them. Conditions cannot be added or edited from this reference page.",
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
      ],
    },
  ],
};
