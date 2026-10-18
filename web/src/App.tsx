import { useEffect, useMemo, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, signInWithPassword, watchUser } from "./auth";
import type { Config } from "./types";
import { AppProvider, identityOf } from "./store";
import PrintHost from "./components/PrintHost";
import Shell from "./components/Shell";
import { BrandMark } from "./components/Icons";
import { Motif, Skeleton } from "./components/Motif";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authEnabled);
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
      <main className="signin" id="main">
        <Motif className="signin-motif" width={216} height={76} />
        <span className="brand-mark big" aria-hidden="true"><BrandMark size={26} /></span>
        <h1>Yukdhis</h1>
        <p className="muted">Autonomous Compliance Engineer for banking</p>
        <button className="primary" onClick={() => signIn()}>Sign in with Google</button>
        <form className="signin-form" onSubmit={submitPassword}>
          <p className="muted">No Google account? Use the test account.</p>
          <input type="email" autoComplete="username" placeholder="Email" aria-label="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input type="password" autoComplete="current-password" placeholder="Password" aria-label="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          {signInError && <div className="banner error" role="alert">{signInError}</div>}
          <button type="submit">Sign in with email</button>
        </form>
      </main>
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
