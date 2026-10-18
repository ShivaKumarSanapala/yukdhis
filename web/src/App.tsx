import { useEffect, useMemo, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, signInWithPassword, watchUser } from "./auth";
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
  // Optional build-time demo credentials (VITE_DEMO_EMAIL / VITE_DEMO_PASSWORD) prefill the form for evaluators.
  const [email, setEmail] = useState<string>(import.meta.env.VITE_DEMO_EMAIL ?? "");
  const [password, setPassword] = useState<string>(import.meta.env.VITE_DEMO_PASSWORD ?? "");
  const [signInError, setSignInError] = useState("");
  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault(); setSignInError("");
    try { await signInWithPassword(email, password); } catch { setSignInError("Email or password is incorrect."); }
  };

  useEffect(() => watchUser((u) => { setUser(u); setReady(true); }), []);
  useEffect(() => {
    if (ready && (!authEnabled || user)) api.config().then(setConfig).catch((e) => setError(e.message));
  }, [ready, user]);
  const identity = useMemo(() => identityOf(user?.displayName, user?.email ?? config?.user), [user, config]);

  if (authEnabled && ready && !user) {
    return (
      <LandingPage
        onSignIn={() => signIn()}
        testLogin={
          <form className="grid gap-2 rounded-xl border border-zinc-800/50 bg-zinc-900/50 p-4 backdrop-blur-md" onSubmit={submitPassword}>
            <p className="text-center text-sm text-zinc-400" style={{ margin: 0 }}>No Google account? Use the test account.</p>
            <input className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-base text-white" type="email" autoComplete="username" placeholder="Email" aria-label="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-base text-white" type="password" autoComplete="current-password" placeholder="Password" aria-label="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            {signInError && <div className="text-sm text-red-400" role="alert">{signInError}</div>}
            <button type="submit" className="rounded-lg border border-zinc-700 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800">Sign in with email</button>
          </form>
        }
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
