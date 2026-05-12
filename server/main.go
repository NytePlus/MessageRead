package main

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"html"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Message struct {
	ID        string
	ToName    string
	Body      string
	CreatedAt int64
	ReadAt    *int64
}

type createPayload struct {
	ToName string `json:"toName"`
	Body   string `json:"body"`
}

var (
	mu       sync.RWMutex
	store    = map[string]*Message{}
	distOnce sync.Once
	distPath string
)

func trimStr(s string) string { return strings.TrimSpace(s) }

func nanoID(length int) string {
	const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
	dest := make([]byte, length)
	buf := make([]byte, length)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())[:length]
	}
	for i := 0; i < length; i++ {
		dest[i] = alphabet[int(buf[i])%len(alphabet)]
	}
	return string(dest)
}

func listenPort() string {
	p := trimStr(os.Getenv("PORT"))
	p = strings.TrimPrefix(p, ":")
	if p == "" {
		return "4000"
	}
	return p
}

func publicBaseURL() string {
	raw := strings.TrimSuffix(trimStr(os.Getenv("PUBLIC_BASE_URL")), "/")
	if raw != "" {
		return raw
	}
	return "http://localhost:" + listenPort()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func applyCORS(w http.ResponseWriter, r *http.Request) {
	origin := r.Header.Get("Origin")
	if origin != "" {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Allow-Credentials", "true")
	} else {
		w.Header().Set("Access-Control-Allow-Origin", "*")
	}
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
}

func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		applyCORS(w, r)
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func handleCreate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var p createPayload
	if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "请求体无效。"})
		return
	}
	toName := trimStr(p.ToName)
	body := trimStr(p.Body)
	if toName == "" || body == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "需要提供对方名字和要说的内容。"})
		return
	}

	id := nanoID(12)
	now := time.Now().UnixMilli()
	msg := &Message{ID: id, ToName: toName, Body: body, CreatedAt: now}

	mu.Lock()
	store[id] = msg
	mu.Unlock()

	base := publicBaseURL()
	writeJSON(w, http.StatusOK, map[string]string{
		"id":        id,
		"openUrl":   base + "/open/" + id,
		"statusUrl": base + "/api/messages/" + id + "/status",
	})
}

func handleStatus(w http.ResponseWriter, r *http.Request, id string) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	mu.RLock()
	m, ok := store[id]
	mu.RUnlock()
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "消息不存在。"})
		return
	}

	var readAt any = nil
	if m.ReadAt != nil {
		readAt = *m.ReadAt
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"read":      m.ReadAt != nil,
		"readAt":    readAt,
		"createdAt": m.CreatedAt,
	})
}

func page404HTML() []byte {
	return []byte("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\"/><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/><title>未找到</title></head><body style=\"font-family:system-ui;padding:24px;\"><p>链接无效或消息已过期。</p></body></html>")
}

func openPageHTML(m *Message, firstVisit bool) []byte {
	name := html.EscapeString(m.ToName)
	text := strings.ReplaceAll(html.EscapeString(m.Body), "\n", "<br/>")
	badge := `<p style="color:#64748b;font-size:14px;margin-top:8px;">欢迎再次访问。</p>`
	if firstVisit {
		badge = `<p style="color:#16a34a;font-size:14px;margin-top:8px;">你正在首次打开这封信。</p>`
	}
	var b strings.Builder
	b.Grow(1500 + len(text))
	b.WriteString("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n  <meta charset=\"UTF-8\"/>\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n  <title>给 ")
	b.WriteString(name)
	b.WriteString(" 的信</title>\n  <style>\n    body { margin: 0; min-height: 100vh; font-family: \"Segoe UI\", system-ui, sans-serif; background: linear-gradient(160deg,#0f172a 0%,#1e293b 55%,#0ea5e9 160%); color: #f8fafc; display: flex; align-items: center; justify-content: center; padding: 24px; box-sizing: border-box; }\n    .card { max-width: 420px; width: 100%; background: rgba(15,23,42,.72); backdrop-filter: blur(10px); border: 1px solid rgba(148,163,184,.25); border-radius: 16px; padding: 28px 24px; box-shadow: 0 25px 50px -12px rgba(0,0,0,.45); }\n    h1 { font-size: 1.15rem; margin: 0 0 12px; font-weight: 600; letter-spacing: .02em; }\n    .to { color: #7dd3fc; }\n    .body { line-height: 1.7; font-size: 1.05rem; color: #e2e8f0; margin-top: 8px; }\n  </style>\n</head>\n<body>\n  <div class=\"card\">\n    <h1>致 <span class=\"to\">")
	b.WriteString(name)
	b.WriteString("</span></h1>\n    ")
	b.WriteString(badge)
	b.WriteString("\n    <div class=\"body\">")
	b.WriteString(text)
	b.WriteString("</div>\n  </div>\n</body>\n</html>")
	return []byte(b.String())
}

func handleOpen(w http.ResponseWriter, r *http.Request, id string) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	mu.Lock()
	m, ok := store[id]
	var first bool
	if ok && m != nil {
		first = m.ReadAt == nil
		if first {
			ts := time.Now().UnixMilli()
			m.ReadAt = &ts
		}
	}
	mu.Unlock()

	if !ok || m == nil {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write(page404HTML())
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(openPageHTML(m, first))
	}
}

func resolveWebDist() string {
	distOnce.Do(func() {
		if v := trimStr(os.Getenv("WEB_DIST")); v != "" {
			if abs, err := filepath.Abs(v); err == nil {
				distPath = abs
				return
			}
			distPath = v
			return
		}
		wd, err := os.Getwd()
		if err != nil {
			distPath = ""
			return
		}
		candidates := []string{
			filepath.Join(wd, "client", "dist"),
			filepath.Join(wd, "..", "client", "dist"),
		}
		for _, c := range candidates {
			if abs, err := filepath.Abs(c); err == nil {
				if fi, err := os.Stat(abs); err == nil && fi.IsDir() {
					distPath = abs
					return
				}
			}
		}
		distPath = filepath.Join(wd, "client", "dist")
	})
	return distPath
}

func underDist(distRootAbs string, cand string) bool {
	candAbs, err := filepath.Abs(cand)
	if err != nil {
		return false
	}
	rootAbs, err := filepath.Abs(distRootAbs)
	if err != nil {
		return false
	}
	rel, err := filepath.Rel(rootAbs, candAbs)
	if err != nil {
		return false
	}
	return rel == "." || rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func spaHandler(distRoot string, api http.Handler) http.Handler {
	index := filepath.Join(distRoot, "index.html")
	distRootAbs, err := filepath.Abs(distRoot)
	if err != nil {
		distRootAbs = distRoot
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") || strings.HasPrefix(r.URL.Path, "/open/") {
			api.ServeHTTP(w, r)
			return
		}

		switch r.Method {
		case http.MethodGet, http.MethodHead:
		default:
			http.NotFound(w, r)
			return
		}

		rel := strings.TrimPrefix(filepath.ToSlash(r.URL.Path), "/")
		for _, seg := range strings.Split(rel, "/") {
			if seg == "" {
				continue
			}
			if seg == "." || seg == ".." || strings.Contains(seg, "\\") {
				http.NotFound(w, r)
				return
			}
		}

		cand := filepath.Join(distRootAbs, filepath.FromSlash(rel))
		absCand, errAp := filepath.Abs(cand)
		if errAp != nil || !underDist(distRootAbs, absCand) {
			http.NotFound(w, r)
			return
		}
		if fi, statErr := os.Stat(absCand); statErr == nil && !fi.IsDir() {
			http.ServeFile(w, r, absCand)
			return
		}
		http.ServeFile(w, r, index)
	})
}

func main() {
	port := listenPort()
	addr := ":" + port

	apiMux := http.NewServeMux()
	apiMux.HandleFunc("POST /api/messages", handleCreate)

	apiMux.HandleFunc("GET /api/messages/{id}/status", func(w http.ResponseWriter, r *http.Request) {
		handleStatus(w, r, r.PathValue("id"))
	})
	apiMux.HandleFunc("HEAD /api/messages/{id}/status", func(w http.ResponseWriter, r *http.Request) {
		handleStatus(w, r, r.PathValue("id"))
	})

	apiMux.HandleFunc("GET /open/{id}", func(w http.ResponseWriter, r *http.Request) {
		handleOpen(w, r, r.PathValue("id"))
	})
	apiMux.HandleFunc("HEAD /open/{id}", func(w http.ResponseWriter, r *http.Request) {
		handleOpen(w, r, r.PathValue("id"))
	})

	handler := http.Handler(apiMux)

	d := resolveWebDist()
	if fi, err := os.Stat(d); err == nil && fi.IsDir() {
		handler = spaHandler(d, apiMux)
		log.Printf("已托管前端静态资源: %s", d)
	} else if d != "" {
		log.Printf("未找到 WEB_DIST/client/dist（仅 API）: %s", d)
	}

	log.Printf("监听 %s  —  PUBLIC_BASE_URL=%s", addr, publicBaseURL())
	if err := http.ListenAndServe(addr, withCORS(handler)); err != nil {
		log.Fatal(err)
	}
}
