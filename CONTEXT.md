# Context

Origin pitch and framing for ShadowFleet OS. Captured verbatim from the founding note so future work can refer back to the original intent.

---

## Framing

Yes, this does directly relate to national security and defense, but you have to frame it correctly.

Don't pitch it as: *"AI for detecting illegal oil shipping."*

Pitch it as: *"AI-powered maritime deception detection for gray-zone warfare, sanctions enforcement, and naval domain awareness."*

That sounds way more Palantir, defense, and national security.

## The core defense problem

Adversaries like Russia, Iran, North Korea, and proxy networks use normal-looking commercial ships to hide military-relevant activity. They spoof AIS, change flags, fake ownership, conduct ship-to-ship transfers, move sanctioned oil, move dual-use goods, fund war, and create uncertainty in maritime zones.

The U.S. Treasury has directly sanctioned shadow-fleet vessels tied to Russian oil exports and Iranian petroleum networks because that revenue supports adversary regimes and threatens U.S. interests.
- https://home.treasury.gov/news/press-releases/jy2777

The national security link is not just oil. Oil is the funding mechanism. The deeper issue is: **the military and intelligence community cannot trust the maritime picture.**

AIS is supposed to say where a ship is, what it is, and where it's going — but AIS can be turned off or spoofed. USNI specifically notes that ships are increasingly turning off AIS or spoofing locations, which creates a maritime domain awareness gap.
- https://www.usni.org/magazines/proceedings/2025/september/move-beyond-ais-maritime-domain-awareness

NATO also treats maritime situational awareness as part of security operations, counter-terrorism, and safe maritime control, so this isn't just a trade/compliance issue.
- https://www.nato.int/en/what-we-do/operations-and-missions/natos-maritime-activities

## The pitch

Modern adversaries don't always attack with warships. They use commercial vessels, fake identities, spoofed locations, shell companies, and ship-to-ship transfers to move money, fuel, weapons, and influence through the ocean. Our system detects when the maritime story doesn't match reality.

## The solution

**ShadowFleet OS:** an AI maritime deception engine that fuses AIS, satellite imagery, IMO registry history, ownership records, port calls, route plausibility, ship-to-ship proximity, flag changes, sanctions lists, and behavioral history to score vessels for deception risk and generate an evidence-backed interdiction brief.

## The demo

A vessel claims it is following a normal route. The system shows:

1. AIS says the vessel was near India.
2. Satellite imagery shows a matching tanker near a Russian export route.
3. It went dark for 11 hours near a known ship-to-ship transfer zone.
4. Its flag changed twice in 6 months.
5. Its ownership shell company is connected to previously sanctioned vessels.
6. Its route makes no commercial sense based on port history.
7. Output: *"High-risk maritime deception. Likely shadow fleet transfer. Confidence: 84%. Recommended action: monitor, flag for sanctions review, notify maritime command."*

That's Palantir-coded because it's not just "AI detects ship." It's multi-source intelligence fusion → anomaly detection → decision support → evidence package — the same pattern as previous hackathon winners (intelligence generation, drone detection, tracking, operational decision support).

## Why this beats the drone idea

Drone detection is more obviously defense, but it's also crowded. Shadow fleets are more unique, and you can make it defense-relevant by tying it to: adversary financing, sanctions evasion, gray-zone warfare, weapons logistics, naval domain awareness, undersea cable risk, and coalition interdiction.

## One-liners

- *"We help commanders detect when a ship is lying."*
- *"ShadowFleet OS turns messy maritime data into an evidence-backed threat assessment, so naval and intelligence teams can identify spoofed vessels, illicit transfers, and gray-zone logistics before they disappear into the global supply chain."*

That's the angle. Don't let it sound like customs enforcement. Make it about trusting the battlespace map at sea. That's national defense.
