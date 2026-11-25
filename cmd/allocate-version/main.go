package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/pixelwave/grok-manager/internal/app"
)

func main() {
	counter, err := app.LoadReleaseCounter("release-counter.json")
	if err != nil {
		exit(err)
	}
	previous, err := os.ReadFile("VERSION")
	if err != nil {
		exit(err)
	}
	version, next, err := app.NextVersion(counter, time.Now())
	if err != nil {
		exit(err)
	}
	if err := os.WriteFile("VERSION", []byte(version+"\n"), 0o644); err != nil {
		exit(err)
	}
	if err := app.SaveReleaseCounter("release-counter.json", next); err != nil {
		exit(err)
	}
	if err := replaceFallback(strings.TrimSpace(string(previous)), version); err != nil {
		exit(err)
	}
	fmt.Printf("%s\n", version)
}

func replaceFallback(previous, version string) error {
	const path = "cmd/grok-manager/main.go"
	body, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	pattern := regexp.MustCompile(`var Version = "` + regexp.QuoteMeta(previous) + `"`)
	updated := pattern.ReplaceAll(body, []byte(`var Version = "`+version+`"`))
	if string(updated) == string(body) {
		return fmt.Errorf("fallback version in %s was not %s", path, previous)
	}
	return os.WriteFile(path, updated, 0o644)
}

func exit(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
