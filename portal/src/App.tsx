import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BaseStyles, ThemeProvider } from "@primer/react";
import { AuthProvider } from "./auth/AuthProvider";
import { OrgProvider } from "./auth/OrgProvider";
import { AuthRoute, ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./auth/LoginPage";
import { SignUpPage } from "./auth/SignUpPage";
import { PasswordResetPage } from "./auth/PasswordResetPage";
import { CLIAuthPage } from "./auth/CLIAuthPage";
import { AuthorizePage } from "./auth/AuthorizePage";
import { AppShell } from "./components/layout/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { TeamPage } from "./pages/TeamPage";
import { GroupsPage } from "./pages/GroupsPage";
import { GroupDetailPage } from "./pages/GroupDetailPage";
import { NewGroupPage } from "./pages/NewGroupPage";
import { EditGroupPage } from "./pages/EditGroupPage";
import { NewEntryPage } from "./pages/NewEntryPage";
import { EditEntryPage } from "./pages/EditEntryPage";
import { EditNodePage } from "./pages/EditNodePage";
import { CompliancePage } from "./pages/CompliancePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SuperAdminsPage } from "./pages/SuperAdminsPage";
import { LicencePage } from "./pages/LicencePage";
import { OrganisationsPage } from "./pages/OrganisationsPage";
import { ServicesPage } from "./pages/ServicesPage";
import { ServiceDetailPage } from "./pages/ServiceDetailPage";
import { InferencePage } from "./pages/InferencePage";
import { WelcomePage } from "./pages/WelcomePage";
import { AppsPage } from "./pages/AppsPage";
import { AppDetailPage } from "./pages/AppDetailPage";
import { VmProfilesPage } from "./pages/VmProfilesPage";
import { VmCommandsPage } from "./pages/VmCommandsPage";
import { CloudLimitsPage } from "./pages/CloudLimitsPage";
import { VmCredentialsPage } from "./pages/VmCredentialsPage";
import { ApiKeysPage } from "./pages/ApiKeysPage";
import { ErasuresPage } from "./pages/ErasuresPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider colorMode="auto">
        <BaseStyles>
          <AuthProvider>
            <OrgProvider>
              <BrowserRouter>
                <Routes>
                  {/* The portal opens on its dashboard; someone signed
                  out is sent on to sign in. The public site — landing
                  page and documentation — is a separate repository
                  (igni-site) served at ignite-ember.sh, and the
                  sidebar links to its documentation there. */}
                  <Route index element={<Navigate to="/dashboard" replace />} />
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/signup" element={<SignUpPage />} />
                  <Route
                    path="/reset-password"
                    element={<PasswordResetPage />}
                  />
                  <Route element={<AuthRoute />}>
                    <Route path="cli-auth" element={<CLIAuthPage />} />
                    {/* Signing a person in to a registered service —
                    igni's OAuth authorization endpoint. AuthRoute, not
                    ProtectedRoute, so a signed-out visitor comes back
                    here with the request's query intact. */}
                    <Route path="authorize" element={<AuthorizePage />} />
                  </Route>
                  <Route element={<ProtectedRoute />}>
                    <Route element={<AppShell />}>
                      <Route path="dashboard" element={<DashboardPage />} />
                      <Route path="team" element={<TeamPage />} />
                      <Route path="groups" element={<GroupsPage />} />
                      <Route path="groups/new" element={<NewGroupPage />} />
                      <Route path="groups/:id" element={<GroupDetailPage />} />
                      <Route
                        path="groups/:id/edit"
                        element={<EditGroupPage />}
                      />
                      <Route
                        path="groups/:id/entries/new"
                        element={<NewEntryPage />}
                      />
                      <Route
                        path="groups/:id/entries/:entryId/edit"
                        element={<EditEntryPage />}
                      />
                      <Route path="apps" element={<AppsPage />} />
                      <Route path="apps/:appId" element={<AppDetailPage />} />
                      <Route path="vm-profiles" element={<VmProfilesPage />} />
                      <Route path="vm-commands" element={<VmCommandsPage />} />
                      <Route path="cloud-limits" element={<CloudLimitsPage />} />
                      <Route path="vm-credentials" element={<VmCredentialsPage />} />
                      <Route path="api-keys" element={<ApiKeysPage />} />
                      <Route path="erasures" element={<ErasuresPage />} />
                      <Route path="compliance" element={<CompliancePage />} />
                      <Route path="inference" element={<InferencePage />} />
                      <Route
                        path="inference/nodes/:nodeId/edit"
                        element={<EditNodePage />}
                      />
                      <Route path="settings" element={<SettingsPage />} />
                      <Route path="super-admins" element={<SuperAdminsPage />} />
                      <Route path="licence" element={<LicencePage />} />
                      <Route path="organisations" element={<OrganisationsPage />} />
                      <Route path="services" element={<ServicesPage />} />
                      <Route path="services/:clientId" element={<ServiceDetailPage />} />
                      <Route path="welcome" element={<WelcomePage />} />
                    </Route>
                  </Route>
                  {/* The portal build had no catch-all, so a stale or
                  mistyped URL rendered a blank page — which reads as
                  an outage rather than a wrong address. `/dashboard`
                  rather than `/`, because anyone reaching a portal URL
                  is signed in or about to be. */}
                  <Route path="*" element={<Navigate to="/dashboard" replace />} />
                </Routes>
              </BrowserRouter>
            </OrgProvider>
          </AuthProvider>
        </BaseStyles>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
