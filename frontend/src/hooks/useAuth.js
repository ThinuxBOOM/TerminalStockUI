import { useContext } from "react";
import { AuthContext } from "../auth/AuthProvider";

function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export { useAuth };
export default useAuth;
