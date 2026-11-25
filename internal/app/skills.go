package app

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Skill is one custom skill on this machine, from ~/.grok/skills.
type Skill struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Disabled    bool   `json:"disabled"`
}

var skillNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$`)

func skillsDir() string {
	if dir := strings.TrimSpace(os.Getenv("GROK_SKILLS_DIR")); dir != "" {
		return dir
	}
	home := homeDir()
	if home == "" {
		return ""
	}
	return filepath.Join(home, ".grok", "skills")
}

func grokConfigPath() string {
	if path := strings.TrimSpace(os.Getenv("GROK_CONFIG")); path != "" {
		return path
	}
	home := homeDir()
	if home == "" {
		return ""
	}
	return filepath.Join(home, ".grok", "config.toml")
}

// ListSkills reads the custom skills on this machine. A missing directory is an empty list.
func ListSkills() []Skill {
	root := skillsDir()
	if root == "" {
		return []Skill{}
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return []Skill{}
	}
	disabled := map[string]bool{}
	for _, name := range readDisabled(grokConfigPath()) {
		disabled[name] = true
	}
	skills := []Skill{}
	for _, entry := range entries {
		name := entry.Name()
		if !skillNameRE.MatchString(name) {
			continue
		}
		dir := filepath.Join(root, name)
		info, err := os.Lstat(dir)
		if err != nil {
			continue
		}
		if !info.IsDir() && info.Mode()&os.ModeSymlink == 0 {
			continue
		}
		summary, ok := skillSummary(filepath.Join(dir, "SKILL.md"))
		if !ok {
			continue
		}
		skills = append(skills, Skill{Name: name, Description: summary, Disabled: disabled[name]})
	}
	sort.Slice(skills, func(i, j int) bool { return skills[i].Name < skills[j].Name })
	return skills
}

// SetSkillDisabled records the skill name in Grok's disabled list, or clears it.
// The skill folder stays on disk.
func SetSkillDisabled(name string, disabled bool) (int, map[string]any) {
	if !knownSkill(name) {
		return 404, map[string]any{"error": "Skill not found"}
	}
	path := grokConfigPath()
	if path == "" {
		return 500, map[string]any{"error": "Could not update that skill."}
	}
	names := withSkill(readDisabled(path), name, disabled)
	if err := writeDisabled(path, names); err != nil {
		return 500, map[string]any{"error": "Could not update that skill."}
	}
	return 200, map[string]any{"skills": ListSkills()}
}

// DeleteSkill removes one custom skill folder on this machine.
// A symlink is removed without following it. Bundled skills are not in this directory.
func DeleteSkill(name string) (int, map[string]any) {
	dir, ok := skillPath(name)
	if !ok {
		return 404, map[string]any{"error": "Skill not found"}
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return 404, map[string]any{"error": "Skill not found"}
	}
	if info.Mode()&os.ModeSymlink != 0 {
		err = os.Remove(dir)
	} else if info.IsDir() {
		err = os.RemoveAll(dir)
	} else {
		return 404, map[string]any{"error": "Skill not found"}
	}
	if err != nil {
		return 500, map[string]any{"error": "Could not delete that skill."}
	}
	path := grokConfigPath()
	if path != "" {
		if _, err := os.Stat(path); err == nil {
			_ = writeDisabled(path, withSkill(readDisabled(path), name, false))
		}
	}
	return 200, map[string]any{"skills": ListSkills()}
}

func knownSkill(name string) bool {
	_, ok := skillPath(name)
	return ok
}

func skillPath(name string) (string, bool) {
	if !skillNameRE.MatchString(name) {
		return "", false
	}
	root := skillsDir()
	if root == "" {
		return "", false
	}
	dir := filepath.Join(root, name)
	rel, err := filepath.Rel(root, dir)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", false
	}
	info, err := os.Lstat(dir)
	if err != nil || (!info.IsDir() && info.Mode()&os.ModeSymlink == 0) {
		return "", false
	}
	if _, err := os.Stat(filepath.Join(dir, "SKILL.md")); err != nil {
		return "", false
	}
	return dir, true
}

func withSkill(names []string, name string, disabled bool) []string {
	out := make([]string, 0, len(names)+1)
	seen := false
	for _, item := range names {
		if item == name {
			seen = true
			if disabled {
				out = append(out, item)
			}
			continue
		}
		out = append(out, item)
	}
	if disabled && !seen {
		out = append(out, name)
	}
	return out
}

func skillSummary(path string) (string, bool) {
	body, err := os.ReadFile(path)
	if err != nil || !utf8.Valid(body) {
		return "", false
	}
	front, ok := frontmatter(string(body))
	if !ok {
		return "", false
	}
	short := ""
	description := ""
	folding := false
	for _, line := range strings.Split(front, "\n") {
		if folding {
			if strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") {
				description = strings.TrimSpace(description + " " + strings.TrimSpace(line))
				continue
			}
			folding = false
		}
		key, value, isKey := splitYAML(line)
		if !isKey {
			continue
		}
		switch key {
		case "short-description":
			short = unquoteTOML(value)
		case "description":
			if value == "" || value == ">" || value == "|" || value == ">-" || value == "|-" {
				folding = true
				description = ""
			} else {
				description = unquoteTOML(value)
			}
		}
	}
	if strings.TrimSpace(short) != "" {
		return strings.Join(strings.Fields(short), " "), true
	}
	return displaySentence(description), true
}

func frontmatter(text string) (string, bool) {
	text = strings.TrimPrefix(text, "\uFEFF")
	if !strings.HasPrefix(text, "---") {
		return "", false
	}
	rest := strings.TrimPrefix(text, "---")
	rest = strings.TrimPrefix(rest, "\n")
	end := strings.Index(rest, "\n---")
	if end < 0 {
		return "", false
	}
	return rest[:end], true
}

func firstSentence(text string) string {
	text = strings.Join(strings.Fields(text), " ")
	for i := 0; i < len(text); i++ {
		if text[i] == '.' && (i == len(text)-1 || text[i+1] == ' ') {
			return text[:i+1]
		}
	}
	return text
}

// displaySentence keeps a row to one readable line. A short-description is preferred
// by the caller. A long first sentence that opens with a clause before a colon uses that clause.
func displaySentence(text string) string {
	sentence := firstSentence(text)
	if utf8.RuneCountInString(sentence) <= 140 {
		return sentence
	}
	if colon := strings.Index(sentence, ":"); colon >= 20 {
		head := strings.TrimSpace(sentence[:colon])
		if utf8.RuneCountInString(head) <= 140 {
			return head
		}
	}
	cut := sentence
	if utf8.RuneCountInString(cut) > 140 {
		runes := []rune(cut)
		cut = strings.TrimSpace(string(runes[:140]))
		if space := strings.LastIndex(cut, " "); space > 40 {
			cut = strings.TrimSpace(cut[:space])
		}
		return cut + "…"
	}
	return sentence
}

func readDisabled(path string) []string {
	if path == "" {
		return nil
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(string(body), "\n")
	start := -1
	for i, line := range lines {
		if strings.TrimSpace(line) == "[skills]" {
			start = i
			break
		}
	}
	if start < 0 {
		return nil
	}
	end := len(lines)
	for i := start + 1; i < len(lines); i++ {
		trim := strings.TrimSpace(lines[i])
		if strings.HasPrefix(trim, "[") && strings.HasSuffix(trim, "]") {
			end = i
			break
		}
	}
	for i := start + 1; i < end; i++ {
		key, _, ok := splitAssign(lines[i])
		if !ok || key != "disabled" {
			continue
		}
		blob := lines[i]
		j := i
		for strings.Count(blob, "[") > strings.Count(blob, "]") && j+1 < end {
			j++
			blob += "\n" + lines[j]
		}
		_, value, ok := splitAssign(blob)
		if !ok {
			return nil
		}
		return parseStringList(value)
	}
	return nil
}

func writeDisabled(path string, names []string) error {
	body, err := os.ReadFile(path)
	text := ""
	if err == nil {
		text = string(body)
	} else if !os.IsNotExist(err) {
		return err
	}
	updated := putDisabled(text, names)
	if !strings.HasSuffix(updated, "\n") {
		updated += "\n"
	}
	return writeReplace(path, []byte(updated))
}

func putDisabled(text string, names []string) string {
	line := "disabled = " + formatStringList(names)
	lines := strings.Split(text, "\n")
	start := -1
	for i, item := range lines {
		if strings.TrimSpace(item) == "[skills]" {
			start = i
			break
		}
	}
	if start < 0 {
		base := text
		if base != "" && !strings.HasSuffix(base, "\n") {
			base += "\n"
		}
		if base != "" {
			base += "\n"
		}
		return base + "[skills]\n" + line
	}
	end := len(lines)
	for i := start + 1; i < len(lines); i++ {
		trim := strings.TrimSpace(lines[i])
		if strings.HasPrefix(trim, "[") && strings.HasSuffix(trim, "]") {
			end = i
			break
		}
	}
	for i := start + 1; i < end; i++ {
		key, _, ok := splitAssign(lines[i])
		if !ok || key != "disabled" {
			continue
		}
		j := i
		blob := lines[i]
		for strings.Count(blob, "[") > strings.Count(blob, "]") && j+1 < end {
			j++
			blob += "\n" + lines[j]
		}
		out := append([]string{}, lines[:i]...)
		out = append(out, line)
		out = append(out, lines[j+1:]...)
		return strings.Join(out, "\n")
	}
	out := append([]string{}, lines[:start+1]...)
	out = append(out, line)
	out = append(out, lines[start+1:]...)
	return strings.Join(out, "\n")
}

func splitYAML(line string) (string, string, bool) {
	trim := strings.TrimSpace(line)
	if trim == "" || strings.HasPrefix(trim, "#") {
		return "", "", false
	}
	key, value, ok := strings.Cut(trim, ":")
	if !ok {
		return "", "", false
	}
	key = strings.TrimSpace(key)
	if key == "" || strings.ContainsAny(key, " \t#") {
		return "", "", false
	}
	return key, strings.TrimSpace(value), true
}

func splitAssign(line string) (string, string, bool) {
	trim := strings.TrimSpace(line)
	if trim == "" || strings.HasPrefix(trim, "#") {
		return "", "", false
	}
	key, value, ok := strings.Cut(trim, "=")
	if !ok {
		return "", "", false
	}
	key = strings.TrimSpace(key)
	if key == "" || strings.ContainsAny(key, " \t#") {
		return "", "", false
	}
	return key, strings.TrimSpace(value), true
}

func parseStringList(value string) []string {
	value = strings.TrimSpace(value)
	if !strings.HasPrefix(value, "[") {
		return nil
	}
	var names []string
	var current strings.Builder
	quote := byte(0)
	escaped := false
	for i := 0; i < len(value); i++ {
		char := value[i]
		if quote != 0 {
			if escaped {
				current.WriteByte(char)
				escaped = false
				continue
			}
			if char == '\\' && quote == '"' {
				escaped = true
				continue
			}
			if char == quote {
				names = append(names, current.String())
				current.Reset()
				quote = 0
			} else {
				current.WriteByte(char)
			}
			continue
		}
		if char == '"' || char == '\'' {
			quote = char
		}
	}
	return names
}

func formatStringList(names []string) string {
	parts := make([]string, len(names))
	for i, name := range names {
		parts[i] = strconv.Quote(name)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func unquoteTOML(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 {
		if value[0] == '"' && value[len(value)-1] == '"' {
			if text, err := strconv.Unquote(value); err == nil {
				return text
			}
		}
		if value[0] == '\'' && value[len(value)-1] == '\'' {
			return value[1 : len(value)-1]
		}
	}
	if cut, _, ok := strings.Cut(value, " #"); ok {
		return strings.TrimSpace(cut)
	}
	return value
}
