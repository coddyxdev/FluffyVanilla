package com.fluffyvanilla.stats;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.bukkit.Bukkit;
import org.bukkit.OfflinePlayer;
import org.bukkit.World;
import org.bukkit.advancement.Advancement;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerCommandPreprocessEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.Executors;
import java.util.logging.Level;

public class FluffyVanillaStats extends JavaPlugin implements Listener {

    private HttpServer httpServer;
    private String apiKey;
    private String buildsWorld;
    private String farmsWorld;

    private final Map<String, Long[]> tickTimestamps = new HashMap<>();
    private final Map<String, Double> worldTps = new HashMap<>();
    private int trackerTaskId = -1;
    
    private final List<String> eventQueue = Collections.synchronizedList(new ArrayList<>());
    private final List<String> topPlaytimeCache = new ArrayList<>();

    @Override
    public void onEnable() {
        saveDefaultConfig();
        loadConfigValues();
        startTpsTracker();
        startHttpServer();
        Bukkit.getPluginManager().registerEvents(this, this);
        startPlaytimeUpdater();
        getLogger().info("FluffyVanillaStats enabled on port " + getConfig().getInt("port", 8080));
    }

    @Override
    public void onDisable() {
        if (trackerTaskId != -1) Bukkit.getScheduler().cancelTask(trackerTaskId);
        if (httpServer != null) httpServer.stop(0);
        getLogger().info("FluffyVanillaStats disabled.");
    }

    private void loadConfigValues() {
        FileConfiguration cfg = getConfig();
        apiKey       = cfg.getString("api-key", "");
        buildsWorld  = cfg.getString("builds-world", "world");
        farmsWorld   = cfg.getString("farms-world", "world_farm");
    }

    private void startPlaytimeUpdater() {
        Bukkit.getScheduler().runTaskTimerAsynchronously(this, () -> {
            List<OfflinePlayer> players = Arrays.asList(Bukkit.getOfflinePlayers());
            players.sort((a, b) -> Integer.compare(
                b.getStatistic(org.bukkit.Statistic.PLAY_ONE_MINUTE),
                a.getStatistic(org.bukkit.Statistic.PLAY_ONE_MINUTE)
            ));
            List<String> newCache = new ArrayList<>();
            int limit = Math.min(20, players.size());
            for(int i=0; i<limit; i++) {
                OfflinePlayer p = players.get(i);
                long hours = p.getStatistic(org.bukkit.Statistic.PLAY_ONE_MINUTE) / (20L * 60 * 60);
                String pName = p.getName() != null ? p.getName() : "Unknown";
                String skinName = getSkinName(pName);
                newCache.add(String.format("{\"player\":\"%s\",\"skin\":\"%s\",\"hours\":%d}", pName, skinName, hours));
            }
            synchronized(topPlaytimeCache) {
                topPlaytimeCache.clear();
                topPlaytimeCache.addAll(newCache);
            }
        }, 0L, 20L * 60 * 5); // every 5 minutes
    }

    private void startTpsTracker() {
        final int SAMPLES = 100;
        trackerTaskId = Bukkit.getScheduler().scheduleSyncRepeatingTask(this, () -> {
            long now = System.currentTimeMillis();
            for (World world : Bukkit.getWorlds()) {
                String name = world.getName();
                tickTimestamps.putIfAbsent(name, new Long[SAMPLES]);
                Long[] buf = tickTimestamps.get(name);
                System.arraycopy(buf, 1, buf, 0, SAMPLES - 1);
                buf[SAMPLES - 1] = now;
                if (buf[0] != null) {
                    long elapsed = now - buf[0];
                    if (elapsed > 0) {
                        double tps = Math.min(20.0, (SAMPLES * 1000.0) / elapsed);
                        worldTps.put(name, Math.round(tps * 10.0) / 10.0);
                    }
                }
            }
        }, 0L, 1L);
    }
    private double getWorldTps(String worldName) { return worldTps.getOrDefault(worldName, 20.0); }
    private double getServerTps() {
        try { return Math.round(Math.min(20.0, Bukkit.getServer().getTPS()[0]) * 10.0) / 10.0; }
        catch (Exception e) { return 20.0; }
    }
    private double getMspt() {
        try { return Math.round(Bukkit.getServer().getAverageTickTime() * 100.0) / 100.0; }
        catch (Exception e) { return 0.0; }
    }

    private void startHttpServer() {
        int port = getConfig().getInt("port", 8080);
        try {
            httpServer = HttpServer.create(new InetSocketAddress("0.0.0.0", port), 10);
            httpServer.setExecutor(Executors.newCachedThreadPool());
            httpServer.createContext("/stats", this::handleStats);
            httpServer.createContext("/health", this::handleHealth);
            httpServer.createContext("/execute", this::handleExecute);
            httpServer.createContext("/events", this::handleEvents);
            httpServer.createContext("/top-playtime", this::handleTopPlaytime);
            httpServer.createContext("/player", this::handlePlayerStats);
            httpServer.start();
        } catch (IOException e) {
            getLogger().log(Level.SEVERE, "Failed to start HTTP server on port " + port, e);
        }
    }

    private boolean checkAuth(HttpExchange exchange) throws IOException {
        if (!apiKey.isEmpty()) {
            String header = exchange.getRequestHeaders().getFirst("X-API-Key");
            if (!apiKey.equals(header)) {
                send(exchange, 403, "{\"error\":\"Forbidden\"}");
                return false;
            }
        }
        return true;
    }

    private void handleStats(HttpExchange exchange) throws IOException {
        if (!checkAuth(exchange)) return;
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) { send(exchange, 405, "{\"error\":\"Method not allowed\"}"); return; }

        StringBuilder worldsJson = new StringBuilder("{");
        List<World> worlds = Bukkit.getWorlds();
        for (int i = 0; i < worlds.size(); i++) {
            World w = worlds.get(i);
            worldsJson.append("\"").append(w.getName()).append("\":").append(getWorldTps(w.getName()));
            if (i < worlds.size() - 1) worldsJson.append(",");
        }
        worldsJson.append("}");
        double tpsBuilds = worldTps.containsKey(buildsWorld) ? getWorldTps(buildsWorld) : getServerTps();
        double tpsFarms  = worldTps.containsKey(farmsWorld) ? getWorldTps(farmsWorld) : getServerTps();

        String json = String.format(Locale.US,
            "{\"online\":%d,\"max\":%d,\"tps\":%.1f,\"tps_builds\":%.1f,\"tps_farms\":%.1f,\"mspt\":%.2f,\"worlds\":%s}",
            Bukkit.getOnlinePlayers().size(), Bukkit.getMaxPlayers(), getServerTps(), tpsBuilds, tpsFarms, getMspt(), worldsJson
        );
        send(exchange, 200, json);
    }
    
    private void handleEvents(HttpExchange exchange) throws IOException {
        if (!checkAuth(exchange)) return;
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) { send(exchange, 405, "{\"error\":\"Method not allowed\"}"); return; }
        
        StringBuilder json = new StringBuilder("[");
        synchronized (eventQueue) {
            for (int i = 0; i < eventQueue.size(); i++) {
                json.append(eventQueue.get(i));
                if (i < eventQueue.size() - 1) json.append(",");
            }
            eventQueue.clear();
        }
        json.append("]");
        send(exchange, 200, json.toString());
    }
    
    private void handleTopPlaytime(HttpExchange exchange) throws IOException {
        if (!checkAuth(exchange)) return;
        StringBuilder json = new StringBuilder("[");
        synchronized(topPlaytimeCache) {
            for (int i = 0; i < topPlaytimeCache.size(); i++) {
                json.append(topPlaytimeCache.get(i));
                if (i < topPlaytimeCache.size() - 1) json.append(",");
            }
        }
        json.append("]");
        send(exchange, 200, json.toString());
    }
    
    private void handlePlayerStats(HttpExchange exchange) throws IOException {
        if (!checkAuth(exchange)) return;
        String query = exchange.getRequestURI().getQuery();
        String nick = query != null && query.startsWith("name=") ? query.substring(5) : "";
        OfflinePlayer p = Bukkit.getOfflinePlayer(nick);
        if (!p.hasPlayedBefore() && !p.isOnline()) {
            send(exchange, 404, "{\"error\":\"Not found\"}");
            return;
        }
        long hours = p.getStatistic(org.bukkit.Statistic.PLAY_ONE_MINUTE) / (20L * 60 * 60);
        int deaths = p.getStatistic(org.bukkit.Statistic.DEATHS);
        int kills = p.getStatistic(org.bukkit.Statistic.MOB_KILLS);
        
        int advCount = 0;
        if (p.isOnline()) {
            Iterator<Advancement> it = Bukkit.advancementIterator();
            while(it.hasNext()) {
                Advancement adv = it.next();
                if (adv.getKey().getKey().startsWith("story/") && p.getPlayer().getAdvancementProgress(adv).isDone()) {
                    advCount++;
                }
            }
        }
        
        String escapedNick = p.getName() != null ? p.getName().replace("\"", "\\\"") : nick;
        String skinName = getSkinName(escapedNick);
        String json = String.format(Locale.US,"{\"player\":\"%s\",\"skin\":\"%s\",\"hours\":%d,\"deaths\":%d,\"kills\":%d,\"advancements\":%d}",
            escapedNick, skinName, hours, deaths, kills, advCount);
        send(exchange, 200, json);
    }

    private void handleHealth(HttpExchange exchange) throws IOException { send(exchange, 200, "{\"status\":\"ok\"}"); }

    private void handleExecute(HttpExchange exchange) throws IOException {
        if (apiKey.isEmpty() || !checkAuth(exchange)) {
            send(exchange, 403, "{\"error\":\"Forbidden\"}");
            return;
        }
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) { send(exchange, 405, "{\"error\":\"Method not allowed\"}"); return; }
        byte[] bodyBytes = exchange.getRequestBody().readAllBytes();
        String command = new String(bodyBytes, StandardCharsets.UTF_8).trim();
        if (command.isEmpty()) { send(exchange, 400, "{\"error\":\"Empty command\"}"); return; }
        Bukkit.getScheduler().callSyncMethod(this, () -> Bukkit.dispatchCommand(Bukkit.getConsoleSender(), command));
        send(exchange, 200, "{\"status\":\"executed\"}");
    }

    private void send(HttpExchange ex, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.getResponseHeaders().set("Access-Control-Allow-Origin", "*");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(bytes); }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onChat(AsyncPlayerChatEvent e) {
        String p = e.getPlayer().getName();
        String m = e.getMessage().replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
        String json = String.format(Locale.US,"{\"type\":\"chat\",\"player\":\"%s\",\"message\":\"%s\"}", p, m);
        eventQueue.add(json);
    }
    
    @EventHandler(priority = EventPriority.HIGHEST)
    public void onCommand(PlayerCommandPreprocessEvent e) {
        String cmd = e.getMessage().trim();
        if (cmd.toLowerCase().startsWith("/help")) {
            e.setCancelled(true);
            String reason = cmd.length() > 5 ? cmd.substring(5).trim() : "Без причины";
            reason = reason.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
            org.bukkit.Location loc = e.getPlayer().getLocation();
            String json = String.format(Locale.US,"{\"type\":\"help\",\"player\":\"%s\",\"reason\":\"%s\",\"x\":%d,\"y\":%d,\"z\":%d,\"world\":\"%s\"}",
                e.getPlayer().getName(), reason, loc.getBlockX(), loc.getBlockY(), loc.getBlockZ(), loc.getWorld().getName());
            eventQueue.add(json);
            e.getPlayer().sendMessage("§a[Fluffy] §fВаша заявка на помощь отправлена администрации!");
        }
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (cmd.getName().equalsIgnoreCase("fluffystats")) {
            sender.sendMessage("§dFluffyVanillaStats §7— статистика:");
            sender.sendMessage("§fОнлайн: §e" + Bukkit.getOnlinePlayers().size() + "/" + Bukkit.getMaxPlayers());
            sender.sendMessage("§fTPS (сервер): §e" + getServerTps());
            sender.sendMessage("§fTPS (" + buildsWorld + "): §e" + getWorldTps(buildsWorld));
            sender.sendMessage("§fTPS (" + farmsWorld + "): §e" + getWorldTps(farmsWorld));
            sender.sendMessage("§fMSPT: §e" + getMspt());
            return true;
        }
        if (cmd.getName().equalsIgnoreCase("fluffyreload")) {
            reloadConfig();
            loadConfigValues();
            sender.sendMessage("§dFluffyVanillaStats §aконфигурация перезагружена.");
            return true;
        }
        return false;
    }

    /**
     * Tries to get the actual skin name from SkinsRestorer (v14 and v15).
     * Falls back to the player's own name if the plugin is absent or the call fails.
     */
    private String getSkinName(String playerName) {
        // ── SkinsRestorer v14 ──────────────────────────────────────────────────
        try {
            Class<?> apiClass = Class.forName("net.skinsrestorer.api.SkinsRestorerAPI");
            Object api = apiClass.getMethod("getApi").invoke(null);
            Object skin = apiClass.getMethod("getSkinName", String.class).invoke(api, playerName);
            if (skin != null && !skin.toString().trim().isEmpty()) {
                return skin.toString().trim();
            }
        } catch (Throwable ignored) {}

        // ── SkinsRestorer v15 ──────────────────────────────────────────────────
        try {
            // SkinAPIV2 lookup: SkinsRestorerProvider.get().getSkinStorage().getSkinNameOfPlayer(uuid)
            Class<?> providerClass = Class.forName("net.skinsrestorer.api.SkinsRestorerProvider");
            Object srApi = providerClass.getMethod("get").invoke(null);
            Object storage = srApi.getClass().getMethod("getSkinStorage").invoke(srApi);

            // Resolve UUID from online player first, then offline
            java.util.UUID uuid = null;
            Player online = Bukkit.getPlayerExact(playerName);
            if (online != null) {
                uuid = online.getUniqueId();
            } else {
                OfflinePlayer op = Bukkit.getOfflinePlayer(playerName);
                uuid = op.getUniqueId();
            }

            // Optional<String> getSkinNameOfPlayer(UUID)
            Object optional = storage.getClass()
                .getMethod("getSkinNameOfPlayer", java.util.UUID.class)
                .invoke(storage, uuid);
            if (optional != null) {
                java.util.Optional<?> opt = (java.util.Optional<?>) optional;
                if (opt.isPresent() && !opt.get().toString().trim().isEmpty()) {
                    return opt.get().toString().trim();
                }
            }
        } catch (Throwable ignored) {}

        // ── Fallback ───────────────────────────────────────────────────────────
        return playerName;
    }
}
