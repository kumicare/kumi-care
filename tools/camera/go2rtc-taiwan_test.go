package xiaomi

import "testing"

func TestTaiwanEndpoint(t *testing.T) {
 for region, want := range map[string]string{"tw":"https://tw.api.io.mi.com/app", "cn":"https://api.io.mi.com/app", "sg":"https://sg.api.io.mi.com/app"} {
 if got := GetBaseURL(region); got != want { t.Fatalf("region %s: got %s want %s", region, got, want) }
 }
}
