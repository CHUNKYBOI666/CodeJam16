import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

export function createApi() {
  if (!url || !publishableKey) throw new Error("Set the public Supabase URL and publishable key.");
  if (!publishableKey.startsWith("sb_publishable_")) {
    throw new Error("VITE_SUPABASE_PUBLISHABLE_KEY must be a publishable key.");
  }

  const client = createClient(url, publishableKey);
  function unwrap({ data, error }) {
    if (error) throw error;
    return data;
  }

  async function rpc(name, args) {
    return unwrap(await client.rpc(name, args));
  }

  return {
    async session() {
      return unwrap(await client.auth.getSession()).session;
    },
    async signIn() {
      return unwrap(await client.auth.signInAnonymously()).session;
    },
    watchSession(callback) {
      client.auth.onAuthStateChange((event, session) => {
        if (event === "SIGNED_OUT" && !session) queueMicrotask(callback);
      });
    },
    async member(id) {
      return unwrap(await client.from("people").select("id,name").eq("id", id).maybeSingle());
    },
    join(name, code) {
      return rpc("join_room", { p_name: name, p_room_code: code })
        .then((person) => Array.isArray(person) ? person[0] : person);
    },
    invite() {
      return rpc("get_invite_code");
    },
    snapshot() {
      return rpc("room_snapshot");
    },
    serverTime() {
      return rpc("server_time");
    },
    action(id, action, outcome) {
      const args = { p_id: id };
      if (action === "resolve") args.p_outcome = outcome;
      return rpc(`${action}_check`, args);
    },
    resetChecks() {
      return rpc("reset_checks");
    },
    async subscribe(id, listeners) {
      await client.realtime.setAuth();
      const channel = client.channel("welfare-room", {
        config: { private: true, presence: { key: id, enabled: true } },
      });
      channel.on("postgres_changes", { event: "*", schema: "public", table: "checks" },
        (event) => event.eventType === "DELETE" ? listeners.checksDeleted() : listeners.check(event.new));
      channel.on("postgres_changes", { event: "*", schema: "public", table: "feed_status" },
        (event) => listeners.feed(event.new));
      channel.on("postgres_changes", { event: "*", schema: "public", table: "people" },
        () => listeners.members());
      channel.on("presence", { event: "sync" },
        () => listeners.presence(Object.keys(channel.presenceState())));
      channel.subscribe((status) => {
        listeners.status(status);
        if (status === "SUBSCRIBED") void channel.track({ id });
      });
      return () => client.removeChannel(channel);
    },
  };
}
