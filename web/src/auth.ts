// Firebase Authentication (Google sign-in). Disabled when VITE_FIREBASE_API_KEY is unset.
import { initializeApp } from "firebase/app";
import { GoogleAuthProvider, getAuth, onAuthStateChanged, signInWithPopup, signOut, type User } from "firebase/auth";

const env = import.meta.env;
export const authEnabled = Boolean(env.VITE_FIREBASE_API_KEY);

const auth = authEnabled
  ? getAuth(initializeApp({
      apiKey: env.VITE_FIREBASE_API_KEY,
      authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
      projectId: env.VITE_FIREBASE_PROJECT_ID,
      appId: env.VITE_FIREBASE_APP_ID,
    }))
  : null;

export async function idToken(): Promise<string | null> {
  return auth?.currentUser ? auth.currentUser.getIdToken() : null;
}

export function watchUser(callback: (user: User | null) => void): () => void {
  if (!auth) {
    callback(null);
    return () => undefined;
  }
  return onAuthStateChanged(auth, callback);
}

export const signIn = () => auth && signInWithPopup(auth, new GoogleAuthProvider());
export const logOut = () => auth && signOut(auth);
