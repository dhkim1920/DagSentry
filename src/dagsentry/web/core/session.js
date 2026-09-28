import { state } from "./state.js";

export function storedSession() {
  return {
    token: state.currentUser !== null,
    role: state.currentUser ? state.currentUser.role.toLowerCase() : null,
    displayName: state.currentUser ? state.currentUser.display_name : null,
  };
}

export function clearSession() {
  state.currentUser = null;
}
