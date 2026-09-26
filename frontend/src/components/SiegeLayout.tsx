import { useParams, Outlet, Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getSiege } from "../api/sieges";
import {
  Lock,
  LayoutGrid,
  MessageSquare,
  Users,
  GitCompare,
  Settings,
} from "lucide-react";
import { isSiegeLocked } from "../lib/siege";

export default function SiegeLayout() {
  const { id } = useParams<{ id: string }>();
  const siegeId = Number(id);
  const location = useLocation();

  const { data: siege } = useQuery({
    queryKey: ["siege", siegeId],
    queryFn: () => getSiege(siegeId),
    enabled: Boolean(siegeId),
  });

  const tabs = [
    {
      label: "Board",
      icon: <LayoutGrid className="h-4 w-4" />,
      to: `/sieges/${siegeId}/board`,
    },
    {
      label: "Posts",
      icon: <MessageSquare className="h-4 w-4" />,
      to: `/sieges/${siegeId}/posts`,
    },
    {
      label: "Members",
      icon: <Users className="h-4 w-4" />,
      to: `/sieges/${siegeId}/members`,
    },
    {
      label: "Compare",
      icon: <GitCompare className="h-4 w-4" />,
      to: `/sieges/${siegeId}/compare`,
    },
    {
      label: "Settings",
      icon: <Settings className="h-4 w-4" />,
      to: `/sieges/${siegeId}`,
    },
  ];

  // Determine active tab: Settings matches exactly; others match by path prefix.
  function isActive(to: string): boolean {
    if (to === `/sieges/${siegeId}`) {
      return (
        location.pathname === `/sieges/${siegeId}` ||
        location.pathname === `/sieges/${siegeId}/`
      );
    }
    return location.pathname.startsWith(to);
  }

  return (
    <>
      {/* ── Tab navigation ── */}
      <div className="-mx-4 mb-4 border-b border-slate-200 bg-slate-50 px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        {isSiegeLocked(siege) && (
          <div className="flex items-center gap-2 py-2 text-sm font-medium text-red-700">
            <Lock className="h-4 w-4 shrink-0" />
            This siege is locked — no changes allowed
          </div>
        )}
        <div className="flex gap-1">
          {tabs.map((tab) => (
            <Link
              key={tab.to}
              to={tab.to}
              className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                isActive(tab.to)
                  ? "border-violet-600 text-violet-700"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              {tab.icon}
              {tab.label}
            </Link>
          ))}
        </div>
      </div>

      <Outlet />
    </>
  );
}
