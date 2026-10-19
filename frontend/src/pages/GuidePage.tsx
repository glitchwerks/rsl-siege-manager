import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

const topics = [
  ["start", "Start here"],
  ["website", "Use the website"],
  ["mom-bot", "Use mom-bot"],
  ["messages", "Reminders & messages"],
  ["officers", "For officers"],
  ["help", "Something went wrong"],
] as const;

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="scroll-mt-6 space-y-5 rounded-xl border border-slate-200 bg-white p-6 sm:p-8"
    >
      <h2 id={`${id}-title`} className="text-2xl font-bold text-slate-900">
        {title}
      </h2>
      {children}
      <p className="pt-3 text-sm">
        <a href="#guide-top" className="text-violet-700 underline">
          Back to topics ↑
        </a>
      </p>
    </section>
  );
}

function Steps({ children }: { children: ReactNode }) {
  return (
    <ol className="list-decimal space-y-3 pl-6 marker:font-semibold marker:text-violet-700">
      {children}
    </ol>
  );
}

export default function GuidePage() {
  const { hash } = useLocation();

  useEffect(() => {
    // React renders after the browser's initial anchor lookup on a shared URL.
    const id = hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView();
  }, [hash]);

  return (
    <div id="guide-top" className="min-h-screen bg-slate-50 text-slate-700">
      <a
        href="#guide-content"
        className="sr-only focus:not-sr-only focus:block focus:p-4"
      >
        Skip to guide
      </a>
      <header className="border-b border-slate-200 bg-white">
        <nav
          aria-label="Main navigation"
          className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-4"
        >
          <Link to="/" className="font-semibold text-slate-900">
            RSL Siege Manager
          </Link>
          <Link
            to="/sieges"
            className="rounded-md bg-violet-700 px-4 py-2 text-sm font-medium text-white hover:bg-violet-800"
          >
            Open website
          </Link>
        </nav>
      </header>
      <main id="guide-content" className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <div className="mb-8 max-w-3xl">
          <p className="mb-3 text-sm font-semibold text-violet-700">
            For clanmates • No sign-in needed to read
          </p>
          <h1 className="mb-4 text-4xl font-bold tracking-tight text-slate-900">
            Your clan guide
          </h1>
          <p className="text-lg leading-relaxed">
            Find your siege assignments, choose your post preferences, and use
            mom-bot in Discord. Start below; you do not need any technical
            experience.
          </p>
        </div>
        <div className="grid items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
          <nav
            aria-label="Guide topics"
            className="rounded-xl border border-slate-200 bg-white p-5 lg:sticky lg:top-6"
          >
            <p className="mb-3 font-semibold text-slate-900">
              What do you need help with?
            </p>
            <ul className="space-y-3">
              {topics.map(([id, label]) => (
                <li key={id}>
                  <a
                    className="text-violet-700 underline decoration-violet-200 underline-offset-4 hover:decoration-violet-700"
                    href={`#${id}`}
                  >
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="min-w-0 space-y-6 leading-relaxed">
            <Section id="start" title="Start here">
              <p>
                The <strong>website</strong> holds the clan roster and siege
                plans. <strong>Discord</strong> is where mom-bot shares messages
                and lets you choose your own post preferences.
              </p>
              <Steps>
                <li>
                  Join the clan’s Discord server using the invite from an
                  officer. A server is the clan’s shared space in Discord;
                  channels are the conversations inside it.
                </li>
                <li>
                  Set your post preferences with{" "}
                  <a href="#mom-bot" className="text-violet-700 underline">
                    mom-bot’s buttons
                  </a>
                  . These tell officers which post conditions suit your teams.
                </li>
                <li>
                  Check the latest siege plan and your assigned attack day
                  before placing teams or attacking. If anything is unclear, ask
                  an officer.
                </li>
              </Steps>
              <p className="rounded-lg bg-violet-50 p-4 text-violet-900">
                A preference is a request, not an assignment. Follow the current
                siege plan shared by your officers.
              </p>
            </Section>

            <Section id="website" title="Use the website">
              <h3 className="text-lg font-semibold text-slate-900">
                Sign in with your Discord account
              </h3>
              <Steps>
                <li>
                  Open the clan website and choose <strong>Sign in</strong>,
                  then <strong>Sign in with Discord</strong>.
                </li>
                <li>
                  On Discord’s sign-in screen, use the account you use in the
                  clan server. Review the request and approve it to return to
                  the website.
                </li>
                <li>
                  If access is refused, check that you used the right account
                  and joined the clan server. Ask a clan leader to check your
                  access role.
                </li>
              </Steps>
              <p>
                Anyone can read this guide. The roster and siege plans require
                clan access. A computer is recommended for the assignment board.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Find your assignment
              </h3>
              <Steps>
                <li>
                  Choose <strong>Sieges</strong> in the top menu. Find the date
                  your officers are planning for.
                </li>
                <li>
                  Choose <strong>Board</strong> on that siege’s row to see the
                  building assignments. Select your name in the list on the left
                  to highlight your positions. Note each building and position
                  assigned to you; select your name again to clear the
                  highlight.
                </li>
                <li>
                  Choose the siege’s <strong>Posts</strong> tab to check post
                  assignments and their conditions. A post condition is a
                  restriction on the team that can be placed there.
                </li>
                <li>
                  Choose the siege’s <strong>Members</strong> tab to find your{" "}
                  <strong>Attack Day</strong>. Day 1 and Day 2 describe when you
                  should attack.
                </li>
              </Steps>
              <p>
                <strong>Reserve</strong> means a backup position. Ask an officer
                when to fill it. If your name is missing, or you cannot meet a
                condition, tell an officer before placing a team.
              </p>
              <p>
                These pages are shared clan plans. Leave assignment changes to
                the officers coordinating the siege.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Update your post preferences
              </h3>
              <p>
                The easiest route is{" "}
                <a href="#mom-bot" className="text-violet-700 underline">
                  mom-bot in Discord
                </a>
                . On the website, choose <strong>Members</strong> in the top
                menu, open your name, and find <strong>Post Preferences</strong>
                . Tick the conditions that suit your teams, then choose{" "}
                <strong>Save Preferences</strong>. Look for{" "}
                <strong>Preferences saved.</strong>
              </p>
              <p>
                The top menu’s <strong>Posts</strong> page is for planning post
                priorities. Your personal preferences are on your member page.
                Changing preferences does not move your existing assignments.
              </p>
            </Section>

            <Section id="mom-bot" title="Use mom-bot in Discord">
              <h3 className="text-lg font-semibold text-slate-900">
                Run your first command
              </h3>
              <Steps>
                <li>
                  Open the clan’s Discord server and a channel where bot
                  commands are allowed. Ask an officer which channel to use if
                  you are unsure.
                </li>
                <li>
                  Tap or click the message box and type{" "}
                  <strong>/post-conditions-set</strong>. Choose the matching
                  command for <strong>mom-bot</strong> from Discord’s
                  suggestions.
                </li>
                <li>
                  Send the command. On a computer, press Enter. On a phone, use
                  the send button.
                </li>
              </Steps>
              <p>
                A slash command is a request you send to the bot by starting
                with <strong>/</strong>. Choose it from Discord’s suggestions
                rather than sending it as an ordinary message. Use these
                commands inside the clan server.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Choose and save your conditions
              </h3>
              <Steps>
                <li>
                  The bot shows your current choices and a set of condition
                  buttons. Tap a condition to select it; selected buttons turn
                  green. Tap it again to remove it.
                </li>
                <li>
                  Use <strong>Next ▶</strong> and <strong>◀ Prev</strong> to see
                  more conditions. Your choices carry over between pages.
                </li>
                <li>
                  Read the selected-preferences summary, then choose{" "}
                  <strong>Save</strong>. Wait for{" "}
                  <strong>Preferences saved</strong> before leaving.
                </li>
                <li>
                  Run <strong>/post-conditions-get</strong> to check what is
                  saved.
                </li>
              </Steps>
              <p>
                <strong>Cancel</strong> leaves your saved preferences unchanged.
                The buttons are available for about five minutes. If they stop
                responding, run <strong>/post-conditions-set</strong> again and
                check your choices before saving.
              </p>
              <p>
                These preference replies are visible only to you in Discord.
                Your saved preferences are used by the clan’s website for siege
                planning. You can change your own preferences with the bot.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Member command reference
              </h3>
              <dl className="space-y-4 break-words">
                <div>
                  <dt className="font-semibold text-slate-900">
                    /post-conditions
                  </dt>
                  <dd>See the full list of available post conditions.</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /post-conditions-get
                  </dt>
                  <dd>
                    See your saved preferences. If none are set, use the set
                    command.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /post-conditions-set
                  </dt>
                  <dd>Open the buttons to change your preferences.</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">/ping</dt>
                  <dd>
                    Check whether mom-bot responds. It shows its version and how
                    long it has been running.
                  </dd>
                </div>
              </dl>
            </Section>

            <Section id="messages" title="Reminders & messages">
              <p>
                Mom-bot can post reminders for Hydra, Chimera, Siege, and Hydra
                Tank Week in the clan’s Discord channels. Follow the dates and
                instructions in the latest message.
              </p>
              <p>
                Officers can also send siege assignments and recurring personal
                reminders as <strong>direct messages (DMs)</strong>, which
                appear in your private Discord conversations.
              </p>
              <ul className="list-disc space-y-3 pl-6">
                <li>
                  Allow direct messages from members of the clan server if you
                  want to receive bot DMs. Look in that server’s privacy
                  settings in Discord.
                </li>
                <li>
                  If a reminder did not arrive, check your Discord message
                  requests and whether you have blocked the bot. Ask an officer
                  to check the reminder or assignment delivery.
                </li>
                <li>
                  If your siege-day role looks wrong, tell an officer. A role is
                  a label attached to your Discord account; siege-day labels can
                  be updated from the website’s assignments.
                </li>
                <li>
                  To change or stop a recurring personal reminder, ask an
                  officer.
                </li>
              </ul>
            </Section>

            <Section id="officers" title="For officers">
              <p>
                This section is for clanmates who coordinate sieges or manage
                reminders. Follow your clan’s process before changing a shared
                plan or sending messages.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Keep the roster up to date
              </h3>
              <p>
                Choose <strong>Members</strong> in the top menu, then open a
                member’s name to edit their details and choose{" "}
                <strong>Save</strong>. Use <strong>Add Member</strong> for a new
                clanmate. The
                <strong> Active only</strong> checkbox hides inactive members;
                untick it if someone seems to be missing.
              </p>
              <p>
                To connect website records with Discord accounts, choose
                <strong> Sync Discord</strong>, then{" "}
                <strong>Fetch Preview</strong>. Check each proposed match
                carefully, untick any incorrect or uncertain matches, and choose{" "}
                <strong>Apply Selected</strong>. Have the member try their
                preference command again afterward.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Prepare and share a siege
              </h3>
              <Steps>
                <li>
                  Open <strong>Sieges</strong>. Choose the existing siege for
                  the correct date, or choose <strong>New Siege</strong> if a
                  new one is needed.
                </li>
                <li>
                  Check the siege’s <strong>Settings</strong>, including its
                  date, buildings, and defense scroll count. Choose{" "}
                  <strong>Save Settings</strong> after editing those settings.
                </li>
                <li>
                  Check the siege’s <strong>Members</strong> tab. Review who is
                  participating and each member’s attack day.{" "}
                  <strong>Auto-Assign Attack Days</strong> opens a preview;
                  review it before applying it.
                </li>
                <li>
                  On <strong>Board</strong>, arrange building assignments.{" "}
                  <strong>Preview Auto-fill</strong> proposes assignments for
                  review before you apply them. Check post assignments
                  separately on <strong>Posts</strong>.
                </li>
                <li>
                  Choose <strong>Validate</strong> on the board. Read and fix
                  the reported assignment problems before sharing the plan.
                </li>
                <li>
                  In <strong>Settings → Discord Notifications</strong>, choose{" "}
                  <strong>Generate Images</strong> to prepare images,{" "}
                  <strong>Post to Discord</strong> to share assignment images in
                  clan channels, or <strong>Notify Members</strong> to send
                  individual assignment DMs. Review each confirmation before
                  sending and check the delivery result afterward.
                </li>
              </Steps>
              <p>
                To place a member manually on the board, drag their name from
                the list on the left onto the intended building position.
                Dropping the name changes the shared plan immediately. Use the
                preview’s <strong>Apply</strong> button only when you are ready
                to use the proposed auto-fill assignments, or choose
                <strong> Cancel</strong> to leave them unapplied.
              </p>
              <p>
                A siege marked complete is locked against changes. If you need
                to correct an older siege, agree on the correction with the clan
                leader first.
              </p>
              <h3 className="text-lg font-semibold text-slate-900">
                Manage recurring personal reminders in Discord
              </h3>
              <p>
                The following commands require Discord’s{" "}
                <strong>Manage Server</strong> permission. Being called an
                officer in the clan does not automatically grant that Discord
                permission.
              </p>
              <Steps>
                <li>
                  Choose <strong>/member-notify-add</strong> from mom-bot’s
                  command suggestions.
                </li>
                <li>
                  Fill in <strong>member</strong> using Discord’s member picker,
                  give the reminder a unique <strong>name</strong>, and enter
                  the <strong>message</strong> the member should receive.
                </li>
                <li>
                  Enter <strong>start_date</strong> as year-month-day, for
                  example <strong>2026-11-05</strong>. Enter{" "}
                  <strong>time</strong> in 24-hour <strong>UTC</strong>, for
                  example <strong>18:30</strong>. UTC is the shared time
                  standard; this field does not use your phone or computer’s
                  local time. Check the conversion before sending.
                </li>
                <li>
                  Choose a <strong>cadence</strong>: Weekly (every 7 days),
                  Biweekly (every 14 days), or Monthly. Send the command and
                  check the bot’s confirmation.
                </li>
              </Steps>
              <p>
                Monthly reminders use the start date’s day of the month. If a
                month is too short, the reminder falls on that month’s last day.
              </p>
              <dl className="space-y-4 break-words">
                <div>
                  <dt className="font-semibold text-slate-900">
                    /member-notify-list
                  </dt>
                  <dd>Find the reminders and their names.</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /member-notify-get
                  </dt>
                  <dd>Choose a reminder by name to check its details.</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /member-notify-update
                  </dt>
                  <dd>
                    Choose a reminder by name and supply the fields you want to
                    change. Set <strong>enabled</strong> to False to pause it,
                    or True to resume it.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /member-notify-remove
                  </dt>
                  <dd>
                    Delete the reminder with the supplied name. Use update to
                    pause it if you may need it again.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    /notify-new-members
                  </dt>
                  <dd>
                    Choose <strong>state: On</strong> to receive new-member join
                    alerts in your own DMs, or <strong>Off</strong> to stop your
                    alerts. This changes your subscription.
                  </dd>
                </div>
              </dl>
            </Section>

            <Section id="help" title="Something went wrong">
              <dl className="space-y-6">
                <div>
                  <dt className="font-semibold text-slate-900">
                    I cannot sign in to the website.
                  </dt>
                  <dd>
                    Check that Discord is signed in to the account in the clan
                    server. Ask a clan leader to check your server membership
                    and access role. If sign-in is temporarily unavailable, try
                    again later.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    The bot says my account is not registered.
                  </dt>
                  <dd>
                    Ask a clan admin to add or correct your website member
                    record and connect it to the Discord account you are using.
                    Then run the command again.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    I cannot find a command.
                  </dt>
                  <dd>
                    Use a channel in the clan server and choose mom-bot’s
                    command from the suggestions after typing /. Officer
                    commands require extra permission. Ask an officer if a
                    member command is still missing.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    The buttons stopped working, or my changes disappeared.
                  </dt>
                  <dd>
                    Open a fresh editor with /post-conditions-set. Choose your
                    conditions and press Save. Use /post-conditions-get to
                    verify them. Unsaved choices do not replace your saved
                    preferences.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    The bot reports an error or does not respond.
                  </dt>
                  <dd>
                    Try once more after a short wait. If it still fails, tell an
                    officer which command you used, what the message said, and
                    when it happened.
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-900">
                    My assignment, attack day, or reminder looks wrong.
                  </dt>
                  <dd>
                    Send an officer the siege date and describe what needs
                    checking. Wait for their guidance before changing a
                    placement or attacking on another day.
                  </dd>
                </div>
              </dl>
            </Section>
          </div>
        </div>
      </main>
      <footer className="mx-auto max-w-6xl px-6 pb-10 text-sm text-slate-500">
        Need a hand? Ask an officer in the clan’s Discord server.
      </footer>
    </div>
  );
}
