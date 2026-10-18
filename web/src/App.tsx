import { useEffect, useMemo, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, watchUser } from "./auth";
import type { Config } from "./types";
import { AppProvider, identityOf } from "./store";
import LandingPage from "./components/LandingPage";
import PrintHost from "./components/PrintHost";
import Shell from "./components/Shell";
import { Skeleton } from "./components/Motif";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authEnabled);
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  useEffect(() => watchUser((u) => { setUser(u); setReady(true); }), []);
  useEffect(() => {
    if (ready && (!authEnabled || user)) api.config().then(setConfig).catch((e) => setError(e.message));
  }, [ready, user]);
  const identity = useMemo(() => identityOf(user?.displayName, user?.email ?? config?.user, user?.photoURL), [user, config]);

  if (authEnabled && ready && !user) {
    return (
      <LandingPage
        onSignIn={() => signIn()}
      />
    );
  }
  if (!config) {
    return (
      <main className="boot" id="main">
        {error ? <div className="banner error" role="alert">{error}</div> : <Skeleton rows={3} label="Loading workspace" />}
      </main>
    );
  }
  return (
    <BrowserRouter>
      <AppProvider config={config} identity={identity} signOut={authEnabled ? () => void logOut() : undefined}>
        <Shell />
        <PrintHost />
      </AppProvider>
    </BrowserRouter>
  );
}
