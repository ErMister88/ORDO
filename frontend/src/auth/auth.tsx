import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { storage } from "@/src/utils/storage";
import {
  loginRequest,
  fetchMe,
  setAuthFailureHandler,
  TOKEN_KEY,
  User,
} from "@/src/api/client";
import { queryClient } from "@/src/query-client";

async function clearUserCache() {
  await queryClient.cancelQueries();
  queryClient.clear();
}

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setAuthFailureHandler(async () => {
      await clearUserCache();
      setUser(null);
    });
    (async () => {
      const token = await storage.secureGet<string>(TOKEN_KEY, "");
      if (token) {
        try {
          const me = await fetchMe();
          setUser(me);
        } catch {
          await storage.secureRemove(TOKEN_KEY);
          await clearUserCache();
        }
      }
      setLoading(false);
    })();
    return () => setAuthFailureHandler(null);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const data = await loginRequest(email.trim(), password);
    await clearUserCache();
    await storage.secureSet(TOKEN_KEY, data.access_token);
    setUser(data.user);
    return data.user;
  }, []);

  const refresh = useCallback(async () => {
    try {
      const me = await fetchMe();
      setUser(me);
    } catch {
      /* ignore */
    }
  }, []);

  const signOut = useCallback(async () => {
    await queryClient.cancelQueries();
    await storage.secureRemove(TOKEN_KEY);
    queryClient.clear();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signOut, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
