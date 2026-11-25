package app

import (
	"path/filepath"
	"testing"
	"time"
)

func TestLegacyAndWeeklyVersionsOrder(t *testing.T) {
	if !Newer("2026.46.1", "26.11.13") || !Newer("2026.46.2", "2026.46.1") || !Newer("2026.47.1", "2026.46.9") {
		t.Fatal("weekly release should advance")
	}
	if !Newer("2026.10.1", "2026.9.2") || !Newer("2026.1.1", "2025.52.3") || !Newer("2027.1.1", "2026.53.2") {
		t.Fatal("components are numbers")
	}
	if Newer("2026.46.1", "2026.46.1") || Newer("2026.46.1", "v2026.46.2") || Newer("26.11.13", "2026.46.1") {
		t.Fatal("not a newer release")
	}
}

func TestNextVersionFollowsBerlinWeeks(t *testing.T) {
	loc := mustBerlin(t)
	steps := []struct {
		at   time.Time
		want string
	}{
		{time.Date(2025, 12, 28, 12, 0, 0, 0, loc), "2025.52.1"},
		{time.Date(2025, 12, 29, 0, 30, 0, 0, loc), "2026.1.1"},
		{time.Date(2025, 12, 30, 18, 0, 0, 0, loc), "2026.1.2"},
		{time.Date(2026, 1, 1, 0, 5, 0, 0, loc), "2026.1.3"},
		{time.Date(2026, 10, 28, 9, 0, 0, 0, loc), "2026.44.1"},
		{time.Date(2026, 10, 30, 9, 0, 0, 0, loc), "2026.44.2"},
		{time.Date(2026, 11, 2, 9, 0, 0, 0, loc), "2026.45.1"},
		{time.Date(2026, 12, 27, 12, 0, 0, 0, loc), "2026.52.1"},
		{time.Date(2026, 12, 28, 12, 0, 0, 0, loc), "2026.53.1"},
		{time.Date(2027, 1, 1, 12, 0, 0, 0, loc), "2026.53.2"},
		{time.Date(2027, 1, 4, 12, 0, 0, 0, loc), "2027.1.1"},
	}
	var counter ReleaseCounter
	for _, step := range steps {
		got, next, err := NextVersion(counter, step.at)
		if err != nil {
			t.Fatal(err)
		}
		if got != step.want {
			t.Fatalf("%s: got %s", step.at.Format(time.RFC3339), got)
		}
		counter = next
	}
	if _, _, err := NextVersion(counter, time.Time{}); err == nil {
		t.Fatal("expected missing time")
	}
}

func TestPublicationUsesBerlinNotUTCDate(t *testing.T) {
	got, next, err := NextVersion(ReleaseCounter{}, time.Date(2025, 12, 28, 23, 30, 0, 0, time.UTC))
	if err != nil || got != "2026.1.1" {
		t.Fatal(got, err)
	}
	got, _, err = NextVersion(next, time.Date(2025, 12, 28, 22, 30, 0, 0, time.UTC))
	if err != nil || got != "2025.52.1" {
		t.Fatal(got, err)
	}
}

func TestReleaseCounterRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "release-counter.json")
	loaded, err := LoadReleaseCounter(path)
	if err != nil || loaded != (ReleaseCounter{}) {
		t.Fatal(loaded, err)
	}
	want := ReleaseCounter{ISOYear: 2026, ISOWeek: 46, Release: 2}
	if err := SaveReleaseCounter(path, want); err != nil {
		t.Fatal(err)
	}
	loaded, err = LoadReleaseCounter(path)
	if err != nil || loaded != want {
		t.Fatal(loaded, err)
	}
}

func mustBerlin(t *testing.T) *time.Location {
	t.Helper()
	loc, err := time.LoadLocation("Europe/Berlin")
	if err != nil {
		t.Fatal(err)
	}
	return loc
}
