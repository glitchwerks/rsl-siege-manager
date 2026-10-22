import { Navigate, useLocation } from "react-router-dom";
import GuideDocument from "../components/GuideDocument";
import { guidePages, guideSectionPaths } from "../guides/pages";

export default function GuidePage() {
  const { pathname, hash } = useLocation();
  const page = guidePages.find((item) => item.href === pathname);
  if (!page) return <Navigate to="/guide" replace />;
  const target = hash ? guideSectionPaths[hash.slice(1)] : undefined;
  if (target && target !== pathname)
    return <Navigate to={`${target}${hash === "#help" ? "" : hash}`} replace />;
  return <GuideDocument document={page} />;
}
