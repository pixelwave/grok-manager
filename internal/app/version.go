package app

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

// AssetName is the release file for an OS and CPU.
func AssetName(goos, goarch string) (string, error) {
	var arch string
	switch goarch {
	case "amd64":
		arch = "amd64"
	case "arm64":
		arch = "arm64"
	default:
		return "", errUnsupported("unsupported architecture: " + goarch)
	}
	switch goos {
	case "linux", "darwin":
		return "grok-manager_" + goos + "_" + arch, nil
	case "windows":
		return "grok-manager_windows_" + arch + ".exe", nil
	default:
		return "", errUnsupported("This installer supports Linux, Mac, and Windows.")
	}
}

type unsupported string

func (e unsupported) Error() string { return string(e) }

func errUnsupported(message string) error { return unsupported(message) }

// ReleaseCounter is the last YYYY.W.R allocated for this release stream.
// Zero means no weekly release has been published. Platforms built as one
// release share this counter.
type ReleaseCounter struct {
	ISOYear int `json:"isoYear"`
	ISOWeek int `json:"isoWeek"`
	Release int `json:"release"`
}

// NextVersion allocates the next public version for a publication at publishedAt.
// The instant is converted to Europe/Berlin, then to the ISO week-year and week.
// The first publish in a week is R 1. A later publish that week adds 1.
// An internal or CI build does not call this.
func NextVersion(counter ReleaseCounter, publishedAt time.Time) (string, ReleaseCounter, error) {
	if publishedAt.IsZero() {
		return "", ReleaseCounter{}, errors.New("publication time is missing")
	}
	loc, err := time.LoadLocation("Europe/Berlin")
	if err != nil {
		return "", ReleaseCounter{}, err
	}
	year, week := publishedAt.In(loc).ISOWeek()
	release := 1
	if counter.ISOYear == year && counter.ISOWeek == week && counter.Release >= 1 {
		release = counter.Release + 1
	}
	next := ReleaseCounter{ISOYear: year, ISOWeek: week, Release: release}
	return fmt.Sprintf("%d.%d.%d", year, week, release), next, nil
}

// LoadReleaseCounter reads the persistent counter. A missing file is a zero counter.
func LoadReleaseCounter(path string) (ReleaseCounter, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return ReleaseCounter{}, nil
		}
		return ReleaseCounter{}, err
	}
	var counter ReleaseCounter
	if err := json.Unmarshal(body, &counter); err != nil {
		return ReleaseCounter{}, err
	}
	return counter, nil
}

// SaveReleaseCounter writes the counter by replacing the file.
func SaveReleaseCounter(path string, counter ReleaseCounter) error {
	body, err := json.MarshalIndent(counter, "", "  ")
	if err != nil {
		return err
	}
	body = append(body, '\n')
	return writeReplace(path, body)
}

func writeReplace(path string, body []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, body, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Newer reports whether latest is a higher version than current.
// A current release is YYYY.W.R: ISO week-year, ISO week, and the release
// inside that week. Those three numbers are compared numerically.
// Releases through 26.11.13 stay YY.MM.DD. The first publish of a day counts
// as revision 1. A later publish that day is YY.MM.DD.2, then .3.
// A published number is left unchanged, so both forms stay comparable.
func Newer(latest, current string) bool {
	return compareVersions(latest, current) > 0
}

func compareVersions(a, b string) int {
	left := versionParts(a)
	right := versionParts(b)
	n := len(left)
	if len(right) > n {
		n = len(right)
	}
	for i := 0; i < n; i++ {
		lv, rv := 0, 0
		if i < len(left) {
			lv = left[i]
		}
		if i < len(right) {
			rv = right[i]
		}
		if lv != rv {
			if lv > rv {
				return 1
			}
			return -1
		}
	}
	return 0
}

func versionParts(value string) []int {
	value = strings.TrimPrefix(strings.TrimSpace(value), "v")
	raw := strings.Split(value, ".")
	parts := make([]int, 0, len(raw))
	for _, part := range raw {
		parts = append(parts, leadingInt(part))
	}
	if len(parts) == 0 {
		parts = []int{0}
	}
	if parts[0] >= 1000 {
		for len(parts) < 3 {
			parts = append(parts, 0)
		}
		return parts[:3]
	}
	for len(parts) < 3 {
		parts = append(parts, 0)
	}
	rev := 1
	if len(raw) >= 4 {
		if number, ok := revision(raw[3]); ok {
			rev = number
		}
	}
	return []int{parts[0], parts[1], parts[2], rev}
}

func leadingInt(part string) int {
	number := 0
	for _, char := range part {
		if char < '0' || char > '9' {
			break
		}
		number = number*10 + int(char-'0')
	}
	return number
}

func revision(part string) (int, bool) {
	if part == "" {
		return 0, false
	}
	number := 0
	for _, char := range part {
		if char < '0' || char > '9' {
			return 0, false
		}
		number = number*10 + int(char-'0')
	}
	if number < 2 {
		return 0, false
	}
	return number, true
}
