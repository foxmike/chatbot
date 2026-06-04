import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  fetchMe,
  getAccessToken,
  login,
  signup,
  clearAccessToken,
} from "../api/client";
import type { User } from "../api/client";
import type { ApiError } from "../types";

interface AuthScreenProps {
  onAuthenticated: (user: User) => void;
}

function getErrorDetail(error: unknown) {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof (error as ApiError).detail === "string"
  ) {
    return (error as ApiError).detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Something went wrong. Please try again.";
}

export function AuthScreen({ onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [checkingToken, setCheckingToken] = useState(true);

  useEffect(() => {
    let isMounted = true;

    async function checkExistingToken() {
      const token = getAccessToken();
      if (!token) {
        if (isMounted) setCheckingToken(false);
        return;
      }

      try {
        const user = await fetchMe();
        if (isMounted) {
          onAuthenticated(user);
        }
      } catch {
        clearAccessToken();
        if (isMounted) {
          setCheckingToken(false);
        }
      }
    }

    checkExistingToken();

    return () => {
      isMounted = false;
    };
  }, [onAuthenticated]);

  const switchMode = () => {
    setError(null);
    setMode(mode === "login" ? "signup" : "login");
  };

  const validateEmail = (value: string) =>
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);

  const validate = (): string | null => {
    if (!email.trim()) return "Email is required.";
    if (!validateEmail(email.trim())) return "Enter a valid email address.";
    if (!password) return "Password is required.";
    if (mode === "signup") {
      if (password.length < 8) return "Password must be at least 8 characters.";
      if (password !== confirmPassword) return "Passwords do not match.";
      if (!username.trim() || username.trim().length < 3)
        return "Username must be at least 3 characters.";
    }
    return null;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await signup(
          username.trim(),
          email.trim(),
          password,
        );
      }
      const authenticatedUser = await fetchMe();
      onAuthenticated(authenticatedUser);
    } catch (err) {
      console.error("Authentication failed", err);
      setError(getErrorDetail(err));
    } finally {
      setLoading(false);
    }
  };

  if (checkingToken) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-900 px-4">
        <div className="text-sm text-gray-400">Checking session...</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-900 px-4">
      <div className="w-full max-w-md rounded-lg bg-gray-800 p-8 text-gray-100">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Abracapocus Companion</h1>
          <button
            type="button"
            className="text-sm text-indigo-400 transition hover:text-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 rounded"
            onClick={switchMode}
          >
            {mode === "login" ? "Sign up" : "Log in"}
          </button>
        </div>

        {error ? (
          <div className="mb-4 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          {mode === "signup" ? (
            <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
              Username
              <input
                type="text"
                required
                minLength={3}
                value={username}
                disabled={loading}
                className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                placeholder="Your username"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setUsername(e.target.value)
                }
              />
            </label>
          ) : null}

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Email
            <input
              type="email"
              required
              value={email}
              disabled={loading}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              placeholder="you@example.com"
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setEmail(e.target.value)
              }
            />
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Password
            <input
              type="password"
              required
              minLength={mode === "signup" ? 8 : undefined}
              value={password}
              disabled={loading}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              placeholder={mode === "signup" ? "Min 8 characters" : "Your password"}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setPassword(e.target.value)
              }
            />
          </label>

          {mode === "signup" ? (
            <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
              Confirm password
              <input
                type="password"
                required
                value={confirmPassword}
                disabled={loading}
                className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                placeholder="Re-enter password"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setConfirmPassword(e.target.value)
                }
              />
            </label>
          ) : null}

          <button
            type="submit"
            disabled={loading}
            className="mt-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {loading
              ? "Please wait..."
              : mode === "login"
                ? "Log In"
                : "Sign Up"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default AuthScreen;
