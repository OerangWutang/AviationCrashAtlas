import {
  createContext,
  useContext,
  useCallback,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  login as apiLogin,
  logout as apiLogout,
  getCurrentUser,
  type LoginPayload,
} from "./api";
import { isApiError } from "../../lib/api";
import type { CurrentUser, Role } from "../../types/api";

interface AuthContextValue {
  user: CurrentUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (payload: LoginPayload) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  hasRole: (...roles: Role[]) => boolean;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const {
    data: user,
    isLoading,
  } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await getCurrentUser();
      } catch (err) {
        // 401 = no session — that's a valid unauthenticated state, not an error
        if (isApiError(err) && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });

  const loginMutation = useMutation({
    mutationFn: apiLogin,
    onSuccess: (data) => {
      queryClient.setQueryData(["auth", "me"], data);
    },
  });

  const logoutMutation = useMutation({
    mutationFn: apiLogout,
    onSuccess: () => {
      queryClient.setQueryData(["auth", "me"], null);
      queryClient.clear();
    },
    onError: () => {
      // Even if logout request fails, clear local state
      queryClient.setQueryData(["auth", "me"], null);
      queryClient.clear();
    },
  });

  const isAuthenticated = user != null;

  const hasRole = useCallback(
    (...roles: Role[]) => {
      if (!user) return false;
      return roles.includes(user.role);
    },
    [user],
  );

  const can = useCallback(
    (permission: string) => {
      if (!user) return false;
      return user.permissions[permission] === true;
    },
    [user],
  );

  return (
    <AuthContext.Provider
      value={{
        user: user ?? null,
        isLoading,
        isAuthenticated,
        login: loginMutation.mutateAsync,
        logout: logoutMutation.mutateAsync,
        hasRole,
        can,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
