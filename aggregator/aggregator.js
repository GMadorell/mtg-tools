function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

async function getDeckJson(deckId) {
    const targetUrl = `https://api.moxfield.com/v2/decks/all/${deckId}`;
    const corsProxyUrl = `https://corsproxy.io/?url=${encodeURIComponent(targetUrl)}`;

    const response = await fetch(corsProxyUrl);
    if (!response.ok) {
        throw new Error(`CORS proxy error: ${response.status}`);
    }
    return await response.json();
}

//--- Grouping a deck’s mainboard + commanders into { cardName: totalQty } ---
function groupDeckCards(deckData) {
    function toCards(rawObj) {
        if (!rawObj) return [];
        return Object.entries(rawObj).map(([cardName, attr]) => {
            const quantity = attr.quantity || 0;
            const cardLayout = attr.card?.layout || "";
            let finalName = cardName;
            // For double-faced / non-split, keep just the front half
            if (!["split", "adventure"].includes(cardLayout)) {
                finalName = cardName.split(" // ")[0];
            }
            return { name: finalName, quantity };
        });
    }

    const mainboard = toCards(deckData.mainboard);
    const commanders = toCards(deckData.commanders);

    const counts = {};
    for (const c of [...mainboard, ...commanders]) {
        if (!counts[c.name]) counts[c.name] = 0;
        counts[c.name] += c.quantity;
    }
    return counts;
}

//--- Parse Moxfield CSV => { cardName: ownedCount } ---
function parseCollectionCsv(csvText) {
    const lines = csvText.split(/\r?\n/);
    lines.shift(); // skip header

    const owned = {};
    for (const line of lines) {
        if (!line.trim()) continue;
        // a naive CSV split
        const fields = line.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/);
        if (fields.length < 3) continue;

        const countStr = fields[0].replace(/"/g, "").trim();
        const nameStrRaw = fields[2].replace(/"/g, "").trim();
        const count = parseInt(countStr, 10);
        if (isNaN(count)) continue;

        // For " // " splitting:
        const nameClean = nameStrRaw.split(" // ")[0];
        if (!owned[nameClean]) owned[nameClean] = 0;
        owned[nameClean] += count;
    }
    return owned;
}

//--- Build table HTML for Aggregated, Simple, and Missing(Verbose). ---
function buildAggregatedTableHtml(aggregated) {
    let html = `<table class="table table-bordered table-striped">
    <thead>
      <tr>
        <th>Card Name</th>
        <th>Decks</th>
        <th>Quantity</th>
      </tr>
    </thead>
    <tbody>
  `;

    const sortedNames = Object.keys(aggregated).sort();
    for (const cardName of sortedNames) {
        const data = aggregated[cardName];
        const decksAbbr = Array.from(data.decks).sort().join(", ");
        html += `
      <tr>
        <td>${escapeHtml(cardName)}</td>
        <td>${escapeHtml(decksAbbr)}</td>
        <td>${data.quantity}</td>
      </tr>`;
    }

    html += `</tbody></table>`;
    return html;
}

function buildSimpleTableHtml(aggregated) {
    // columns: Card Name, Quantity
    let html = `<table class="table table-bordered table-striped">
    <thead>
      <tr>
        <th>Card Name</th>
        <th>Quantity</th>
      </tr>
    </thead>
    <tbody>
  `;

    const sortedNames = Object.keys(aggregated).sort();
    for (const cardName of sortedNames) {
        const data = aggregated[cardName];
        html += `
      <tr>
        <td>${escapeHtml(cardName)}</td>
        <td>${data.quantity}</td>
      </tr>`;
    }

    html += `</tbody></table>`;
    return html;
}

function buildMissingVerboseTableHtml(aggregated, owned) {
    // columns: Card Name, Missing, Decks, Required, Have
    let html = `<table class="table table-bordered table-striped">
    <thead>
      <tr>
        <th>Card Name</th>
        <th>Missing</th>
        <th>Decks</th>
        <th>Required</th>
        <th>Have</th>
      </tr>
    </thead>
    <tbody>
  `;

    const sortedNames = Object.keys(aggregated).sort();
    let missingAny = false;

    for (const cardName of sortedNames) {
        const data = aggregated[cardName];
        const needed = data.quantity;
        const have = owned[cardName] || 0;
        if (have < needed) {
            missingAny = true;
            const diff = needed - have;
            const deckStr = Array.from(data.decks).sort().join(", ");
            html += `
        <tr>
          <td>${escapeHtml(cardName)}</td>
          <td>${diff}</td>
          <td>${escapeHtml(deckStr)}</td>
          <td>${needed}</td>
          <td>${have}</td>
        </tr>`;
        }
    }

    if (!missingAny) {
        html += `
      <tr>
        <td colspan="5">Great! You appear to have at least enough copies of all needed cards.</td>
      </tr>`;
    }

    html += `</tbody></table>`;
    return html;
}

//--- The “Missing Cards (Export Friendly)” is plain text ---
function buildMissingExportText(aggregated, owned) {
    let lines = [];
    let missingAny = false;

    const sortedNames = Object.keys(aggregated).sort();
    for (const cardName of sortedNames) {
        const data = aggregated[cardName];
        const needed = data.quantity;
        const have = owned[cardName] || 0;
        if (have < needed) {
            missingAny = true;
            const diff = needed - have;
            lines.push(`${diff} ${cardName}`);
        }
    }

    if (!missingAny) {
        lines.push("Great! You appear to have at least enough copies of all needed cards.");
    }
    return lines.join("\n");
}

$(document).ready(function () {
    let outputCache = {
        aggregated: "",      // HTML table
        simple: "",          // HTML table
        missingVerbose: "",  // HTML table
        missingExport: "",   // text
    };

    // Feature #1: Disable run button if no deck URLs
    function toggleRunButton() {
        const deckUrls = $("#deckUrls").val().trim();
        $("#runAggregator").prop("disabled", deckUrls.length === 0);
    }
    $("#deckUrls").on("input", toggleRunButton);
    toggleRunButton();

    $("#outputTabs button.nav-link").on("click", function () {
        // Mark this tab as active
        $("#outputTabs button.nav-link").removeClass("active");
        $(this).addClass("active");

        const mode = $(this).data("mode");

        // For aggregated/simple/missingVerbose => we have HTML (as they're tables)
        // For missingExport => we have text
        if (mode === "missingExport") {
            $("#outputArea").text(outputCache[mode] || "No data available yet.");
        } else {
            $("#outputArea").html(outputCache[mode] || "<p>No data available yet.</p>");
        }
    });

    $("#copyOutput").on("click", function () {
        const textToCopy = $("#outputArea").text();
        if (!textToCopy.trim()) {
            alert("There is no output to copy!");
            return;
        }

        navigator.clipboard.writeText(textToCopy).then(() => {
            alert("Output copied to clipboard!");
        }, err => {
            alert("Failed to copy text: " + err);
        });
    });

    $("#runAggregator").on("click", async function () {
        outputCache = {
            aggregated: "",
            simple: "",
            missingVerbose: "",
            missingExport: "",
        };
        $("#outputArea").text("");

        $("#outputsContainer").addClass("disabledOutput");
        $("#copyOutput").prop("disabled", true);

        $("#spinner").show();

        try {
            const deckUrlLines = $("#deckUrls").val().trim().split("\n");

            // Parse CSV (if any)
            let collectionCsvText = "";
            const fileInput = $("#collectionFile")[0];
            if (fileInput.files && fileInput.files[0]) {
                collectionCsvText = await fileInput.files[0].text();
            }
            const ownedCards = collectionCsvText ? parseCollectionCsv(collectionCsvText) : {};

            const aggregatedData = {}; // { cardName: { quantity, decks: Set<string> } }
            async function aggregateDeck(url) {
                const match = url.match(/\/decks\/(?:all\/)?([A-Za-z0-9_-]+)/);
                if (!match) {
                    throw new Error("Invalid Moxfield deck URL: " + url);
                }
                const deckId = match[1];

                const deckJson = await getDeckJson(deckId);
                console.log("Fetched deck:", deckId, deckJson);

                // Use the deck name or commander name for abbreviation
                let deckAbbr = deckJson.name ? deckJson.name.split(" ")[0] : "Deck";
                if (deckJson.commanders) {
                    const commanderNames = Object.keys(deckJson.commanders);
                    if (commanderNames.length > 0) {
                        deckAbbr = commanderNames[0].split(" ")[0];
                    }
                }
                deckAbbr = deckAbbr.replace(",", "");

                // Merge counts
                const groupedCounts = groupDeckCards(deckJson);
                for (const [cardName, qty] of Object.entries(groupedCounts)) {
                    if (!aggregatedData[cardName]) {
                        aggregatedData[cardName] = { quantity: 0, decks: new Set() };
                    }
                    aggregatedData[cardName].quantity += qty;
                    aggregatedData[cardName].decks.add(deckAbbr);
                }
            }

            for (const url of deckUrlLines) {
                const trimmed = url.trim();
                if (!trimmed) continue;
                await aggregateDeck(trimmed);
            }

            outputCache.aggregated = buildAggregatedTableHtml(aggregatedData);
            outputCache.simple = buildSimpleTableHtml(aggregatedData);
            outputCache.missingVerbose = buildMissingVerboseTableHtml(aggregatedData, ownedCards);
            outputCache.missingExport = buildMissingExportText(aggregatedData, ownedCards);

            $("#outputTabs button.nav-link").removeClass("active");
            $("#outputTabs button[data-mode='aggregated']").addClass("active");
            $("#outputArea").html(outputCache["aggregated"]);

            $("#copyOutput").prop("disabled", false);
        } catch (err) {
            console.error("Aggregator error:", err);
            $("#outputArea").text("Error: " + err.message);
        } finally {
            $("#spinner").hide();
            $("#outputsContainer").removeClass("disabledOutput");
        }
    });
});
