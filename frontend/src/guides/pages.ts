import { generalGuide } from "./general";
import type { GuideDocument, GuideSection } from "./types";

export interface GuidePageDefinition extends GuideDocument {
  href: string;
}

function section(id: string): GuideSection {
  const found = generalGuide.sections.find((item) => item.id === id);
  if (!found) throw new Error(`Missing guide section: ${id}`);
  return { ...found, title: found.title.replace(/^\d+\. /, "") };
}

const start = section("start");
const help = section("help");
const audience =
  "General Usage Guide · Website & core Discord bot · Available to everyone";

export const guidePages: GuidePageDefinition[] = [
  {
    href: "/guide",
    title: "Quick Start",
    audience,
    intro:
      "Start here to sign in, find your way around, and understand how your clan uses the website and Discord. No technical experience needed.",
    sections: [
      {
        id: "start",
        title: "Sign in & get started",
        summary: "Open your clan’s website with the right Discord account.",
        tasks: [start.tasks[0]],
      },
      {
        id: "navigation",
        title: "Find your way around",
        summary: "Recognize the clan menu and the menu inside a siege.",
        tasks: [start.tasks[1]],
      },
      {
        id: "planning-session",
        title: "From planning to Discord",
        summary:
          "See the main steps before working through the detailed guides.",
        tasks: [start.tasks[2]],
      },
    ],
  },
  {
    href: "/guide/clan-management",
    title: "Clan Management",
    audience,
    intro:
      "Set up your clan’s working information: members, post preferences, and shared post priorities. These are the foundations for planning each siege.",
    sections: [
      {
        id: "clan-setup",
        title: "Set up a new clan",
        summary:
          "Clan setup is handled during deployment or by an administrator of the deployment.",
        tasks: [
          {
            title: "Who sets up a new clan?",
            paragraphs: [
              "Setting up a new clan must be done during the application’s deployment or by an administrator of that deployment.",
              "Contact your deployment administrator to arrange clan setup and get the website address. Once setup is complete, sign in with your clan’s Discord account.",
            ],
            resultTitle: "Who usually handles this?",
            result:
              "This is typically the site developer who handles self-hosting or the cloud deployment of the application.",
          },
        ],
      },
      section("roster"),
      section("preferences"),
      section("priorities"),
    ],
  },
  {
    href: "/guide/siege-management",
    title: "Siege Management",
    audience,
    intro:
      "Work through one siege at a time: create or clone the event, prepare its layout and participants, assign defenses, and share the finished plan through Discord. Always check the siege date first.",
    sections: [
      "create",
      "buildings",
      "siege-members",
      "post-conditions",
      "board",
      "autofill",
      "post-assignments",
      "post-suggestions",
      "compare",
      "discord",
      "lifecycle",
    ].map(section),
  },
  {
    href: "/guide/faq",
    title: "FAQ and Troubleshooting",
    audience,
    intro:
      "Find answers to common questions and work through problems with access, assignments, editing, and Discord delivery.",
    sections: [
      {
        id: "faq",
        title: "Frequently asked questions",
        summary: "Know where to look and who to ask.",
        tasks: [
          {
            title: "Can anyone read the guide?",
            paragraphs: [
              "Yes. The general guide is public. Signing in to view or edit your clan’s records requires your clan’s website access.",
            ],
          },
          {
            title: "Where should I change a member’s information?",
            paragraphs: [
              "Use Clan Management for the main roster, Discord account matching, and personal post preferences. Use Siege Management for participation, attack days, reserve sets, and placements in one event.",
            ],
          },
          {
            title: "How do I get my assignments without website access?",
            paragraphs: [
              "Read the latest assignment images in your clan’s Discord assignment channel, or check your direct messages from the bot. Ask the coordinating officer if your assignment is missing or unclear.",
            ],
          },
        ],
      },
      ...help.tasks.map((task, index) => ({
        id: ["access-help", "editing-help", "suggestions-help"][index],
        title: task.title,
        summary: [
          "Check your account, access role, and the right member list.",
          "Check the event status and any message beside the control.",
          "Review conditions, preferences, and participation.",
        ][index],
        tasks: [task],
      })),
      {
        id: "discord-help",
        title: "Discord delivery problems",
        summary: "Check an account match or a missing assignment message.",
        tasks: [
          {
            title: "Help a member who did not receive a DM",
            steps: [
              "Check their saved Discord username and account match on the main roster. Correct a missing or incorrect record before sending again.",
              "Ask them to check Discord message requests, allow direct messages from server members in that server’s privacy settings, and confirm that they have not blocked the bot.",
              "Read the website’s delivery result. If the service reported an error or an unknown result, ask the website manager to investigate before a broad resend.",
            ],
          },
        ],
      },
    ],
  },
];

export const guideSectionPaths = Object.fromEntries(
  guidePages.flatMap((page) =>
    page.sections.map((item) => [item.id, page.href])
  )
);
// Keep older links to the former troubleshooting section useful.
guideSectionPaths.help = "/guide/faq";
