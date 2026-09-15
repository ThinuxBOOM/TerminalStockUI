import { useCallback, useState } from "react";
import {
  getCurrentUserStub,
  saveStubTier,
  saveStubUserId,
} from "../api/authStub";

// Reactive wrapper around the guest stub (localStorage-backed tier/userId,
// still guest — never gates). Returns { userId, tier, isGuest, setTier,
// setUserId } so Welcome/Login pages and tier stubs share one source.
function useCurrentUserStub() {
  const [snapshot, setSnapshot] = useState(() => getCurrentUserStub());
  const setTier = useCallback((tier) => {
    saveStubTier(tier);
    setSnapshot(getCurrentUserStub());
  }, []);
  const setUserId = useCallback((userId) => {
    saveStubUserId(userId);
    setSnapshot(getCurrentUserStub());
  }, []);
  const refresh = useCallback(() => setSnapshot(getCurrentUserStub()), []);
  return { ...snapshot, setTier, setUserId, refresh };
}

export { useCurrentUserStub };
export default useCurrentUserStub;
