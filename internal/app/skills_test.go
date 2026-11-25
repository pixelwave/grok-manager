package app

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSkillsListDisableAndDelete(t *testing.T) {
	setup(t)
	root := os.Getenv("GROK_SKILLS_DIR")
	writeSkill(t, root, "versioning", "---\nname: versioning\ndescription: Assign public release versions as YYYY.W.R from the publication time in Europe/Berlin. One counter per release stream.\n---\n\n# Versioning\n")
	writeSkill(t, root, "lab-setup", "---\nname: lab-setup\ndescription: Scaffold a lab.\nmetadata:\n  short-description: Scaffold a version lab\n---\n")
	notes := filepath.Join(t.TempDir(), "notes")
	writeSkill(t, notes, "kept", "---\ndescription: Stay outside the skills directory.\n---\n")
	if err := os.Symlink(filepath.Join(notes, "kept"), filepath.Join(root, "kept")); err != nil {
		t.Fatal(err)
	}
	config := "[cli]\nauto_update = true\n\n[ui]\ntheme = \"auto\"\n"
	if err := os.WriteFile(os.Getenv("GROK_CONFIG"), []byte(config), 0o644); err != nil {
		t.Fatal(err)
	}

	skills := ListSkills()
	if len(skills) != 3 || skills[0].Name != "kept" || skills[1].Name != "lab-setup" || skills[2].Name != "versioning" {
		t.Fatalf("%#v", skills)
	}
	if skills[1].Description != "Scaffold a version lab" {
		t.Fatal(skills[1].Description)
	}
	if skills[2].Description != "Assign public release versions as YYYY.W.R from the publication time in Europe/Berlin." {
		t.Fatal(skills[2].Description)
	}

	status, body := SetSkillDisabled("versioning", true)
	if status != 200 {
		t.Fatal(status, body)
	}
	text, err := os.ReadFile(os.Getenv("GROK_CONFIG"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(text), "auto_update = true") || !strings.Contains(string(text), "theme = \"auto\"") {
		t.Fatal(string(text))
	}
	if !strings.Contains(string(text), "disabled = [\"versioning\"]") {
		t.Fatal(string(text))
	}
	if !ListSkills()[2].Disabled {
		t.Fatal("expected versioning disabled")
	}
	if status, _ = SetSkillDisabled("versioning", false); status != 200 {
		t.Fatal(status)
	}
	if ListSkills()[2].Disabled {
		t.Fatal("expected versioning enabled")
	}
	if status, _ = SetSkillDisabled("../versioning", true); status != 404 {
		t.Fatal(status)
	}

	if status, _ = DeleteSkill("kept"); status != 200 {
		t.Fatal(status)
	}
	if _, err := os.Stat(filepath.Join(notes, "kept", "SKILL.md")); err != nil {
		t.Fatal("symlink target was removed")
	}
	if _, err := os.Lstat(filepath.Join(root, "kept")); !os.IsNotExist(err) {
		t.Fatal("symlink still present")
	}
	if status, _ = DeleteSkill("lab-setup"); status != 200 {
		t.Fatal(status)
	}
	if _, err := os.Stat(filepath.Join(root, "lab-setup")); !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if status, _ = DeleteSkill("missing"); status != 404 {
		t.Fatal(status)
	}
}

func TestDisplaySentenceUsesTheOpeningClause(t *testing.T) {
	long := "Scaffold a PLANNR-style version lab into the current project: a dark lab landing with a versioned milestone list, immutable snapshots, a live app link, and a drag-and-drop todo list that reorders items by dropping one onto another. Sets up the lab app."
	if got := displaySentence(long); got != "Scaffold a PLANNR-style version lab into the current project" {
		t.Fatal(got)
	}
	short := "Assign public release versions as YYYY.W.R from the publication time in Europe/Berlin. One counter."
	if got := displaySentence(short); got != "Assign public release versions as YYYY.W.R from the publication time in Europe/Berlin." {
		t.Fatal(got)
	}
}

func TestDisabledListKeepsOtherNames(t *testing.T) {
	setup(t)
	writeSkill(t, os.Getenv("GROK_SKILLS_DIR"), "lab-setup", "---\ndescription: >\n  Scaffold a lab\n  into the project.\n---\n")
	body := "[skills]\ndisabled = [\n  \"other\",\n  \"lab-setup\",\n]\n\n[ui]\ntheme = \"auto\"\n"
	if err := os.WriteFile(os.Getenv("GROK_CONFIG"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	skills := ListSkills()
	if len(skills) != 1 || !skills[0].Disabled || skills[0].Description != "Scaffold a lab into the project." {
		t.Fatalf("%#v", skills)
	}
	if status, _ := SetSkillDisabled("lab-setup", false); status != 200 {
		t.Fatal(status)
	}
	text, err := os.ReadFile(os.Getenv("GROK_CONFIG"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(text), "\"other\"") || strings.Contains(string(text), "lab-setup") || !strings.Contains(string(text), "[ui]") {
		t.Fatal(string(text))
	}
}

func writeSkill(t *testing.T, root, name, body string) {
	t.Helper()
	dir := filepath.Join(root, name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}
