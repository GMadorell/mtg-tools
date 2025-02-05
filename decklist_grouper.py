#!/usr/bin/env python3
"""
Extracts decklists (only mainboard + commanders) from a static list of Moxfield deck URLs.
The script downloads each deck's JSON from the Moxfield API (using urllib),
converts the mainboard and commanders into MTGCard objects,
aggregates the cards from all decks, and prints lists based on selected modes.

Additionally, it can generate a wishlist of cards you are missing to fill these decks,
based on an exported Moxfield CSV collection file.
"""
import os

#---------- SETUP ---------------------
DECK_URLS = os.environ.get("MOXFIELD_DECK_URLS").split(",") # Eg export DECK_URLS="https://moxfield.com/decks/tI_2bfDSKUaZKSsmNoCOTQ,https://moxfield.com/decks/dHKNlWHwUEeX_LnAJsBH9A"
MOXFIELD_COLLECTION_CSV_PATH = os.environ.get("MOXFIELD_CSV_PATH") # Eg export MOXFIELD_CSV_PATH="/Users/user/projects/mtg_tools/some_collection.csv"
#--------------------------------------

# Output mode flags
PRINT_AGGREGATED_LIST = 0                # Aggregated list with deck abbreviations
PRINT_SIMPLE_LIST = 0                    # Simplified {Card Name} {Amount} list
PRINT_MISSING_CARDS_VERBOSE = 1          # Wishlist of missing cards (based on your collection), detailed
PRINT_MISSING_CARDS_EXPORT_FRIENDLY = 0  # Missing cards in {Amount} {CardName} format

import logging
import random
import re
import json
import csv
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List
import urllib.request

# A list of user agents for our HTTP requests.
USER_AGENT_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/93.0.4577.82 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4_2 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/53.0",
]

def extract_deck_id(url: str) -> str:
    """Extract the deck ID from a Moxfield deck URL."""
    m = re.search(r"/decks/(?:all/)?([A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError(f"Could not extract deck id from URL: {url}")
    return m.group(1)

@dataclass
class MTGCard:
    name: str
    quantity: int

    @staticmethod
    def from_json(card_name: str, attr: dict) -> "MTGCard":
        """Create an MTGCard from a JSON entry."""
        quantity = attr.get("quantity", 0)
        card_data = attr.get("card", {})
        layout = card_data.get("layout", "")
        if layout in ("split", "adventure"):
            # Keep the full card name if it's a split or adventure card.
            final_name = card_name
        else:
            # For typical double-faced cards or other cases, keep the front half only.
            final_name = card_name.split(" // ")[0]
        return MTGCard(final_name, quantity)

def to_cards(raw_cards: dict) -> List[MTGCard]:
    """Convert a raw dictionary of cards into a list of MTGCard objects."""
    return [MTGCard.from_json(name, attr) for name, attr in raw_cards.items()]

@dataclass
class DeckList:
    mainboard: List[MTGCard]
    commanders: List[MTGCard]
    name: str = "Unnamed Deck"
    format: str = ""

    @staticmethod
    def from_json(json_data: dict) -> "DeckList":
        """Create a DeckList from the Moxfield API JSON."""
        return DeckList(
            mainboard=to_cards(json_data.get("mainboard", {})),
            commanders=to_cards(json_data.get("commanders", {})),
            name=json_data.get("name", "Unnamed Deck"),
            format=json_data.get("format", ""),
        )

class MoxField:
    """Helper class to interact with the Moxfield API."""
    def get_decklist(self, deck_id: str) -> dict:
        url = f"https://api.moxfield.com/v2/decks/all/{deck_id}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", random.choice(USER_AGENT_LIST))
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))

def group_deck_cards(deck: DeckList) -> Dict[str, int]:
    """
    Combine cards from the mainboard and commanders into a dictionary
    mapping card names to total quantities.
    """
    counts = defaultdict(int)
    for zone in (deck.mainboard, deck.commanders):
        for card in zone:
            counts[card.name] += card.quantity
    return counts

def print_aggregated_results(aggregated: Dict[str, Dict[str, any]]) -> None:
    """Print the aggregated list with deck abbreviations."""
    print("Aggregated decklist with deck abbreviations:")
    for card_name, data in sorted(aggregated.items()):
        decks_list = ", ".join(sorted(abbr.replace(",", "") for abbr in data['decks']))
        print(f"{card_name}: [{decks_list}] {data['quantity']}")
    print()

def print_simple_list(aggregated: Dict[str, Dict[str, any]]) -> None:
    """Print the simple aggregated list."""
    print("Simple aggregated decklist:")
    for card_name, data in sorted(aggregated.items()):
        print(f"{card_name} {data['quantity']}")
    print()

def parse_collection_csv(csv_path: str) -> Dict[str, int]:
    """
    Parse the Moxfield-exported collection CSV file and build a
    dictionary of { card_name: owned_count }.
    Assumes the CSV has columns:
      "Count","Tradelist Count","Name","Edition","Condition",...
    where Name is at index 2 and Count is at index 0.
    """
    owned_cards = defaultdict(int)
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header row
            for row in reader:
                if not row or len(row) < 3:
                    continue
                count_str = row[0].strip().replace('"', '')
                name_str = row[2].strip().replace('"', '')

                # If the CSV name has " // ", drop the second half so it matches the aggregator.
                if " // " in name_str:
                    name_str = name_str.split(" // ")[0]

                if not count_str.isdigit():
                    # If the count column isn't a valid integer, skip
                    continue
                owned_cards[name_str] += int(count_str)

    except FileNotFoundError:
        logging.error(f"CSV file not found: {csv_path}")
    except Exception as e:
        logging.error(f"Error reading CSV: {e}")
    return dict(owned_cards)

def print_missing_cards_verbose(aggregated: Dict[str, Dict[str, any]], collection_path: str) -> None:
    """
    Compare the aggregated deck requirements to what you have in your Moxfield
    CSV collection, and print the cards that are missing (shortfalls) in a more
    detailed, human-readable format:

        Need X '{CardName}', in decks: [{DeckAbbrev1, DeckAbbrev2, ...}],
        required: N, in collection: M
    """
    print("Wishlist (Missing Cards) based on your Moxfield collection [VERBOSE]:")
    owned_cards = parse_collection_csv(collection_path)

    missing_any = False
    for card_name, data in sorted(aggregated.items()):
        needed = data['quantity']
        have = owned_cards.get(card_name, 0)
        if needed > have:
            missing_any = True
            diff = needed - have
            deck_abbr_list = sorted(list(data['decks']))
            deck_abbr_str = ", ".join(deck_abbr_list)
            print(
                f"Need {diff} '{card_name}', "
                f"in decks: [{deck_abbr_str}], "
                f"required: {needed}, "
                f"in collection: {have}"
            )

    if not missing_any:
        print("Great! You appear to have at least enough copies of all needed cards.")
    print()

def print_missing_cards_export_friendly(aggregated: Dict[str, Dict[str, any]], collection_path: str) -> None:
    """
    Compare the aggregated deck requirements to what you have in your Moxfield
    CSV collection, and print the cards that are missing (shortfalls) in a simple,
    export-friendly format:

        {AmountOfCardsMissing} {CardName}
    """
    print("Wishlist (Missing Cards) based on your Moxfield collection [EXPORT-FRIENDLY]:")
    owned_cards = parse_collection_csv(collection_path)

    missing_any = False
    for card_name, data in sorted(aggregated.items()):
        needed = data['quantity']
        have = owned_cards.get(card_name, 0)
        if needed > have:
            missing_any = True
            diff = needed - have
            print(f"{diff} {card_name}")

    if not missing_any:
        print("Great! You appear to have at least enough copies of all needed cards.")
    print()

def main():
    mox = MoxField()
    aggregated = defaultdict(lambda: {'quantity': 0, 'decks': set()})

    # Fetch and aggregate data from each deck
    for url in DECK_URLS:
        try:
            deck_id = extract_deck_id(url)
            print(f"Processing deck id: {deck_id}")
            deck_json = mox.get_decklist(deck_id)
            deck = DeckList.from_json(deck_json)
            # Use first commander's first word or deck name's first word as an abbreviation
            deck_abbr = (deck.commanders[0].name.split()[0] if deck.commanders else deck.name.split()[0]).replace(",", "")
            grouped = group_deck_cards(deck)
            for card_name, qty in grouped.items():
                aggregated[card_name]['quantity'] += qty
                aggregated[card_name]['decks'].add(deck_abbr)
            print(f"Finished processing deck {deck_id}\n{'='*40}\n")
        except Exception as e:
            logging.error(f"Failed to process deck URL '{url}': {e}")

    # Print aggregated or simple list as requested
    if PRINT_AGGREGATED_LIST:
        print_aggregated_results(aggregated)
    if PRINT_SIMPLE_LIST:
        print_simple_list(aggregated)

    # Print the verbose missing-cards list
    if PRINT_MISSING_CARDS_VERBOSE:
        print_missing_cards_verbose(aggregated, MOXFIELD_COLLECTION_CSV_PATH)

    # Print the export-friendly missing-cards list
    if PRINT_MISSING_CARDS_EXPORT_FRIENDLY:
        print_missing_cards_export_friendly(aggregated, MOXFIELD_COLLECTION_CSV_PATH)


if __name__ == "__main__":
    main()
