import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import SiegeLayout from "./components/SiegeLayout";
import LoginPage from "./pages/LoginPage";
import { LandingOrSieges } from "./pages/LandingPage";
import MembersPage from "./pages/MembersPage";
import MemberDetailPage from "./pages/MemberDetailPage";
import SiegesPage from "./pages/SiegesPage";
import SiegeCreatePage from "./pages/SiegeCreatePage";
import SiegeSettingsPage from "./pages/SiegeSettingsPage";
import BoardPage from "./pages/BoardPage";
import PostsPage from "./pages/PostsPage";
import SiegeMembersPage from "./pages/SiegeMembersPage";
import ComparisonPage from "./pages/ComparisonPage";
import PostPrioritiesPage from "./pages/PostPrioritiesPage";
import SystemPage from "./pages/SystemPage";
import GuidePage from "./pages/GuidePage";
import MomBotGuidePage from "./pages/MomBotGuidePage";

function App() {
  return (
    <Routes>
      {/* Public routes — no auth required */}
      <Route path="/" element={<LandingOrSieges />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/guide" element={<GuidePage />} />
      <Route path="/guide/:pageId" element={<GuidePage />} />
      <Route
        path="/guide/mom-bot"
        element={
          <RequireAuth>
            <MomBotGuidePage />
          </RequireAuth>
        }
      />
      <Route
        path="/guide/mom-bot/commands"
        element={
          <RequireAuth>
            <MomBotGuidePage />
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/members" element={<MembersPage />} />
        <Route path="/members/new" element={<MemberDetailPage />} />
        <Route path="/members/:id" element={<MemberDetailPage />} />
        <Route path="/sieges" element={<SiegesPage />} />
        <Route path="/sieges/new" element={<SiegeCreatePage />} />
        <Route path="/sieges/:id" element={<SiegeLayout />}>
          <Route index element={<SiegeSettingsPage />} />
          <Route path="board" element={<BoardPage />} />
          <Route path="posts" element={<PostsPage />} />
          <Route path="members" element={<SiegeMembersPage />} />
          <Route path="compare" element={<ComparisonPage />} />
        </Route>
        <Route path="/post-priorities" element={<PostPrioritiesPage />} />
        <Route path="/system" element={<SystemPage />} />
      </Route>
    </Routes>
  );
}

export default App;
