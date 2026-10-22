import type { GuideImage } from "./types";

// Captured from the actual website with fictional, isolated demo data.
export const guideScreenshots: Record<string, GuideImage[]> = {
  navigation: [
    {
      src: "/guide-images/top-menu.jpg",
      title: "Top menu — the whole clan",
      alt: "Top navigation bar with Sieges, Members, Posts, Guide, and System.",
      caption:
        "Use this bar to move between the clan’s main pages. Members here opens the main clan roster.",
      width: 1280,
      height: 720,
      previewCrop: { top: 0, height: 58 },
    },
    {
      src: "/guide-images/siege-menu.jpg",
      title: "Siege menu — the event you opened",
      alt: "Siege navigation bar with Board, Posts, Members, Compare, and Settings; Board is selected.",
      caption:
        "This second bar appears after you open a siege. Its Members tab applies only to that siege. Enlarge the image to see the siege date below the bar.",
      width: 1265,
      height: 712,
      previewCrop: { top: 58, height: 66 },
    },
  ],
  "read-plan": [
    {
      src: "/guide-images/sieges.jpg",
      title: "Find the right siege",
      alt: "Sieges list with dates, Planning and Complete status badges, and Board, Posts, and Members links.",
      caption:
        "Check the date and status first, then use the links on that row to open the right view.",
      width: 1280,
      height: 440,
    },
  ],
  roster: [
    {
      src: "/guide-images/members.jpg",
      title: "Clan roster controls",
      alt: "Members roster with fictional names, role and power columns, Active only filter, Sync Discord, and Add Member buttons.",
      caption:
        "The top controls manage the main clan roster. Each row has an Edit / Preferences link.",
      width: 1280,
      height: 680,
    },
  ],
  preferences: [
    {
      src: "/guide-images/preferences.jpg",
      title: "Choose and save preferences",
      alt: "Post Preferences panel with condition checkboxes, grouping buttons, and Save Preferences.",
      caption:
        "Select suitable conditions, browse by level or type, and use Save Preferences to store your choices.",
      width: 1265,
      height: 840,
    },
  ],
  create: [
    {
      src: "/guide-images/create-siege.jpg",
      title: "Create or clone a siege",
      alt: "New Siege form with clone dropdown, date field, Create Siege, and Cancel buttons.",
      caption:
        "Choose a previous siege in the clone list or leave it set to create a blank siege.",
      width: 1280,
      height: 540,
    },
  ],
  buildings: [
    {
      src: "/guide-images/buildings.jpg",
      title: "Match the game’s building layout",
      alt: "Building settings showing level buttons and Broken checkboxes for the Stronghold, Mana Shrines, Magic Tower, and Defense Tower.",
      caption:
        "Find the correct building and number. Its level and Broken checkbox save immediately.",
      width: 1265,
      height: 840,
    },
  ],
  "siege-members": [
    {
      src: "/guide-images/siege-members.jpg",
      title: "Attack days, pinned choices, and reserve sets",
      alt: "Siege Members table with attack-day dropdowns, a pinned Day 2 choice, Reserve Set checkboxes, and automatic assignment buttons.",
      caption:
        "Blair’s Day 2 choice is pinned. Reserve Set checkboxes record a separate part of the plan.",
      width: 1280,
      height: 800,
    },
  ],
  board: [
    {
      src: "/guide-images/board.jpg",
      title: "Inspect and place defense teams",
      alt: "Assignment board with Alex selected in the member list and purple outlines around Alex’s positions, plus an empty slot and a RESERVE marker.",
      caption:
        "Selecting a name highlights its placements. This unfinished example also shows empty and reserve positions.",
      width: 1280,
      height: 850,
    },
  ],
  autofill: [
    {
      src: "/guide-images/validation.jpg",
      title: "Read a validation result",
      alt: "Validation dialog over the board with a warning to review unassigned defense positions.",
      caption:
        "Read the message, correct the relevant part of the plan, and run validation again.",
      width: 1280,
      height: 850,
    },
  ],
  "post-conditions": [
    {
      src: "/guide-images/post-conditions.jpg",
      title: "Set a post’s current conditions",
      alt: "Post 1 condition editor expanded with condition checkboxes, Level and Type grouping controls, and Save Conditions.",
      caption:
        "Expand the correct post, select its current game conditions, then choose Save Conditions.",
      width: 1280,
      height: 850,
    },
  ],
  "post-assignments": [
    {
      src: "/guide-images/post-assignments.jpg",
      title: "Review post assignments",
      alt: "Board Posts view showing each post’s priority, condition, description, assigned member, and Suggest Assignments button.",
      caption:
        "Expand a post to choose a member manually, or open Suggest Assignments to review a proposal.",
      width: 1280,
      height: 760,
    },
    {
      src: "/guide-images/post-suggestions.jpg",
      title: "Review selected suggestions before applying",
      alt: "Suggest post assignments dialog showing new assignments and replacements, selectable rows, matched conditions, Cancel, and Apply.",
      caption:
        "Check the current and proposed members, untick unwanted changes, and review the Apply count.",
      width: 1280,
      height: 850,
    },
  ],
  priorities: [
    {
      src: "/guide-images/post-priorities.jpg",
      title: "Maintain shared post priorities",
      alt: "Shared Posts page with priority dropdowns and description fields for six example posts.",
      caption:
        "Priority changes save immediately; a description saves when you leave its field.",
      width: 1280,
      height: 850,
    },
  ],
  "post-expanded": [
    {
      src: "/guide-images/post-expanded-multiple.jpg",
      title: "Expand a post to assign a member",
      alt: "Post 1 expanded with three active conditions. Alex has three matching condition choices, while Blair and Casey each have two, with an Assign button for each member.",
      caption:
        "Alex matches all three conditions; Blair and Casey each match two. Choose one matching condition beside the intended member, then use that member’s Assign button.",
      width: 1265,
      height: 1374,
      previewCrop: { top: 360, height: 560 },
    },
  ],
  "post-duplicate-warning": [
    {
      src: "/guide-images/post-duplicate-warning.jpg",
      title: "Review a duplicate condition before confirming",
      alt: "Alex’s ATK condition at Post 1 is marked Post 2. An Already matched warning appears with Cancel and Confirm anyway buttons, alongside alternative Magic and Void condition choices.",
      caption:
        "Alex is already matched to the ATK condition at Post 2. Trying the same match at Post 1 pauses for confirmation; Magic and Void are alternative matches in this example.",
      width: 1265,
      height: 1404,
      previewCrop: { top: 360, height: 260 },
    },
  ],
  "conditions-reference": [
    {
      src: "/guide-images/conditions-reference.jpg",
      title: "Post conditions reference",
      alt: "Posts page with the Conditions tab selected, a read-only list of champion restrictions, and Level and Type grouping controls.",
      caption:
        "The Conditions tab is a reference list. Use Level or Type to change how the list is grouped.",
      width: 1280,
      height: 720,
      previewCrop: { top: 0, height: 530 },
    },
  ],
  compare: [
    {
      src: "/guide-images/comparison.jpg",
      title: "Compare old and new positions",
      alt: "Comparison page with a completed siege selector, added and removed position counts, Show diffs only, and old and new placement columns.",
      caption:
        "Green shows added placements, red shows removed placements, and gray shows unchanged placements.",
      width: 1280,
      height: 850,
    },
  ],
  discord: [
    {
      src: "/guide-images/settings.jpg",
      title: "Prepare images and send assignments",
      alt: "Siege Settings showing separate Generate Images, Post to Discord, and Notify Members buttons below the Lifecycle controls.",
      caption:
        "Generate Images previews the plan. Post to Discord and Notify Members are separate actions that send messages.",
      width: 1280,
      height: 850,
    },
  ],
  lifecycle: [
    {
      src: "/guide-images/settings.jpg",
      title: "Find the lifecycle controls",
      alt: "Planning siege settings with Start Siege, Clone, and Delete controls above the Discord Notifications panel.",
      caption:
        "The available lifecycle controls depend on whether the siege is Planning, Active, or Complete.",
      width: 1280,
      height: 850,
    },
  ],
};
