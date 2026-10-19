// SPDX-License-Identifier: BSD-2-Clause

using System;
using System.Collections.Generic;
using System.Text;
using ClassicUO.Game;
using ClassicUO.Game.Data;
using ClassicUO.Game.GameObjects;
using ClassicUO.Game.Managers;
using ClassicUO.Game.UI.Gumps;
using ClassicUO.Network;

namespace ClassicUO.Agent
{
    // Banking and shopping without the mouse, each as one request: say the keyword near a
    // banker or vendor, wait for the bank box or the vendor's list, then move the items or
    // answer the list. One errand at a time; its progress is in the snapshot.
    //
    //   bank  {deposit: "gold,loot" (or item:count), withdraw: "bandage:100,heal potion:5"}
    //   buy   {target: vendor, items: "bandage:50,black pearl:20"}
    //   sell  {target: vendor, items: "loot"}  (or item words: "longsword,ring")
    internal sealed class AgentErrands
    {
        private const uint OPEN_TIMEOUT_MS = 6000;
        private const uint LIST_TIMEOUT_MS = 4000;  // a vendor's list comes at once or not at all
        private const int VENDOR_ASKS = 3;          // the first NAMED_ASKS by the vendor's name, the rest as "vendor buy"
        internal const int NAMED_ASKS = 2;
        private const uint MOVE_INTERVAL_MS = 1100; // ModernUO refuses item moves much closer together
        private const int MOVE_TRIES = 4;
        private const uint ERRAND_TIMEOUT_MS = 90_000;
        // A vendor that can't be stood next to (in a shop one tile wide, a townsperson in the only way to it): after
        // this many walks up, its context menu's Buy or Sell is used instead, which ModernUO's vendors answer from
        // MENU_RANGE tiles (VendorBuyEntry, VendorSellEntry). Six walks over 48 s had failed (cuo-cgy).
        private const int MENU_AFTER = 2;
        internal const int MENU_RANGE = 8;
        internal const int CLILOC_BUY = 3006103, CLILOC_SELL = 3006104;

        private readonly World _world;
        private readonly AgentController _agent;
        private readonly Queue<Move> _moves = new Queue<Move>();
        private Move _current;
        private uint _started, _askedAt, _nextStep, _movedAt;
        private int _asks, _approaches;
        private bool _byMenu; // the last ask was the vendor's context menu: its Buy or Sell is chosen when the menu comes
        private uint _vendor;
        private string _vendorName = string.Empty, _said = string.Empty, _nearer = string.Empty;
        private string _items = string.Empty;
        private List<string> _deposit = new List<string>();
        private int _goldBefore;

        public AgentErrands(World world, AgentController agent)
        {
            _world = world;
            _agent = agent;
        }

        public string Kind { get; private set; } = string.Empty;
        public string State { get; private set; } = string.Empty;
        public string Detail { get; private set; } = string.Empty;
        public int Moved { get; private set; }
        public int GoldChange => _world.Player == null ? 0 : (int) _world.Player.Gold - _goldBefore;
        public bool Busy => State == "opening" || State == "moving" || State == "approaching" || State == "waiting";

        // Which vendor a buy or a sale went to and how it was asked, for the snapshot and the brain's log.
        public uint Vendor => _vendor;
        public string VendorName => _vendorName;
        public int Asks => _asks;
        public string Said => _said;
        public string Nearer => _nearer;

        private sealed class Move
        {
            public uint Serial, Dest;
            public ushort Amount;
            public string What;
            public int Tries;
            public uint SentAt;
            public ushort Graphic;
            public int DestBefore; // how many of this graphic the destination held before the move
        }

        // ---------------------------------------------------------------- requests

        public (string, string) StartBank(string deposit, string withdraw)
        {
            Begin("bank");
            _deposit = new List<string>(Split(deposit));
            _items = withdraw ?? string.Empty;

            if (_deposit.Count == 0 && _items.Length == 0)
            {
                return Fail("nothing to deposit or withdraw");
            }

            // The client keeps a bank box it was shown, contents and all, after the server has closed
            // it (the character walked away), and the server refuses whatever is dropped in it then:
            // a soak run's every deposit after the first failed so. Ask the banker each time, and count
            // the box open once it is opened again.
            if (_world.Player.FindItemByLayer(Layer.Bank) is Item known)
            {
                known.Opened = false;
                UIManager.GetGump<ContainerGump>(known.Serial)?.Dispose();
            }

            Ask("bank");

            return ("done", string.Empty);
        }

        public (string, string) StartShop(bool buy, uint vendor, string items)
        {
            Begin(buy ? "buy" : "sell");
            _items = items ?? string.Empty;
            _vendor = vendor != 0 ? vendor : NearestVendor();

            if (_vendor == 0)
            {
                return Fail("no vendor in sight");
            }

            if (_items.Length == 0)
            {
                return Fail(buy ? "nothing to buy" : "nothing to sell");
            }

            _vendorName = _world.Mobiles.Get(_vendor) is Mobile v ? Called(v) : string.Empty;
            State = "approaching";

            return ("done", string.Empty);
        }

        public void Cancel()
        {
            if (Busy)
            {
                Finish("failed", "cancelled");
            }
        }

        private void Begin(string kind)
        {
            Kind = kind;
            State = "opening";
            Detail = string.Empty;
            Moved = 0;
            _moves.Clear();
            _current = null;
            _asks = 0;
            _approaches = 0;
            _byMenu = false;
            _vendor = 0;
            _vendorName = _said = _nearer = string.Empty;
            _started = Time.Ticks;
            _goldBefore = (int) (_world.Player?.Gold ?? 0);
        }

        private (string, string) Fail(string why)
        {
            Finish("failed", why);

            return ("failed", why);
        }

        private void Finish(string state, string detail)
        {
            State = state;
            Detail = detail;
            _moves.Clear();
            _current = null;
            _agent.Journal.AddAgentEvent($"{Kind}: {detail}");
        }

        private void Ask(string words)
        {
            _askedAt = Time.Ticks;
            _asks++;
            _said = words;
            GameActions.Say(words);
        }

        // ---------------------------------------------------------------- per tick

        public void Update(uint now)
        {
            if (!Busy || now < _nextStep || _world.Player == null)
            {
                return;
            }

            _nextStep = now + 200;

            if (now - _started > ERRAND_TIMEOUT_MS)
            {
                Finish("failed", "took too long");

                return;
            }

            switch (State)
            {
                case "opening" when Kind == "bank":
                    if (BankBox() != null)
                    {
                        PlanBank();
                    }
                    else if (now - _askedAt > OPEN_TIMEOUT_MS)
                    {
                        if (_asks < 2)
                        {
                            Ask("bank");
                        }
                        else
                        {
                            Finish("failed", "the bank box did not open: no banker within 12 tiles?");
                        }
                    }

                    break;

                case "approaching":
                    Approach(now);

                    break;

                case "waiting":
                    WaitForList(now);

                    break;

                case "moving":
                    StepMoves(now);

                    break;
            }
        }

        // ---------------------------------------------------------------- bank

        private Item BankBox()
        {
            Item box = _world.Player.FindItemByLayer(Layer.Bank);

            return box != null && (box.Opened || UIManager.GetGump<ContainerGump>(box.Serial) != null) ? box : null;
        }

        private void PlanBank()
        {
            Item box = BankBox();
            Item pack = _world.Player.FindItemByLayer(Layer.Backpack);

            if (box == null || pack == null)
            {
                Finish("failed", "no bank box or backpack");

                return;
            }

            bool gold = _deposit.Contains("gold") || _deposit.Contains("all");
            bool loot = _deposit.Contains("loot") || _deposit.Contains("all");

            for (LinkedObject o = pack.Items; o != null; o = o.Next)
            {
                var it = (Item) o;

                if (gold && it.IsCoin || loot && IsLoot(it) && it.Serial != _agent.HeldWeapon)
                {
                    Enqueue(it, it.Amount, box.Serial, it.IsCoin ? "gold" : AgentSnapshot.NameOf(_world, it));
                }
            }

            // Named supplies to deposit ("bandage:150"): taken from the backpack.
            foreach ((string word, int amount) in Wanted(string.Join(",", _deposit.FindAll(d => d != "gold" && d != "loot" && d != "all"))))
            {
                int left = amount;

                foreach (Item it in Find(pack, word))
                {
                    if (left <= 0)
                    {
                        break;
                    }

                    int put = Math.Min(left, Math.Max((int) it.Amount, 1));
                    Enqueue(it, put, box.Serial, $"{put} {word}");
                    left -= put;
                }
            }

            foreach ((string word, int amount) in Wanted(_items))
            {
                // Banked gold is account balance on AOS-era servers: ask the banker for it.
                if (word == "gold")
                {
                    GameActions.Say($"withdraw {amount}");
                    _movedAt = Time.Ticks;

                    continue;
                }

                int need = amount;

                foreach (Item it in Find(box, word))
                {
                    if (need <= 0)
                    {
                        break;
                    }

                    int take = Math.Min(need, Math.Max((int) it.Amount, 1));
                    Enqueue(it, (ushort) take, pack.Serial, $"{take} {word}");
                    need -= take;
                }

                if (need == amount)
                {
                    Detail += $"no {word} in the bank; ";
                }
            }

            State = "moving";
        }

        private void Enqueue(Item it, int amount, uint dest, string what)
        {
            _moves.Enqueue(new Move { Serial = it.Serial, Amount = (ushort) Math.Max(1, amount), Dest = dest, What = what });
        }

        // Moves one item at a time, checking each landed before the next: the server refuses
        // moves that come too fast ("You must wait to perform another action").
        private void StepMoves(uint now)
        {
            if (_current != null && _current.SentAt != 0)
            {
                // Landed when the destination holds that many more: this covers a whole item
                // moving, part of a stack splitting off, and a stack merging into one there.
                // Gold dropped in a bank box becomes account balance on AOS-era servers: the coins
                // just vanish.
                Item moved = _world.Items.Get(_current.Serial);
                bool landed = _world.Items.Get(_current.Dest) is Item dest && CountIn(dest, _current.Graphic) >= _current.DestBefore + _current.Amount
                              || _current.Graphic == 0x0EED && Kind == "bank" && (moved == null || moved.Container == _current.Dest);

                if (landed)
                {
                    Moved++;
                    _current = null;
                }
                else if (now - _current.SentAt < 1500)
                {
                    return;
                }
                else if (_current.Tries >= MOVE_TRIES)
                {
                    Detail += $"could not move {_current.What}; ";
                    _current = null;
                }
            }

            if (_current == null)
            {
                if (_moves.Count == 0)
                {
                    // A purchase, a sale or a gold withdrawal shows up as a change in gold a moment later.
                    if ((Kind != "bank" || _items.Contains("gold")) && now - _movedAt < 1500)
                    {
                        return;
                    }

                    UIManager.GetGump<ContainerGump>(_world.Player.FindItemByLayer(Layer.Bank)?.Serial ?? 0)?.Dispose();
                    Finish("done", Summary());

                    return;
                }

                _current = _moves.Dequeue();
            }

            if (now - _movedAt < MOVE_INTERVAL_MS || Client.Game.UO.GameCursor?.ItemHold.Enabled == true)
            {
                return;
            }

            Item item = _world.Items.Get(_current.Serial);

            if (item == null || _world.Items.Get(_current.Dest) is not Item target)
            {
                Detail += $"lost track of {_current.What}; ";
                _current = null;

                return;
            }

            // Never what the character wears or holds: in a soak run a recall dropped the katana into the
            // pack as a bank errand listed the loot, the weapon went back on, and the errand banked it
            // from the warrior's hand, which then fought bare-handed until it died.
            if (item.Container == _world.Player.Serial || item.Serial == _agent.HeldWeapon)
            {
                Detail += $"kept {_current.What}: the character's own; ";
                _current = null;

                return;
            }

            // The count to beat is taken on the first try only: a move the server took late
            // still counts when a retry is under way.
            if (_current.Tries++ == 0)
            {
                _current.Graphic = item.Graphic;
                _current.Amount = Math.Min(_current.Amount, Math.Max(item.Amount, (ushort) 1));
                _current.DestBefore = CountIn(target, item.Graphic);
            }

            _current.SentAt = _movedAt = now;
            GameActions.GrabItem(_world, item.Serial, _current.Amount, _current.Dest);
        }

        private string Summary()
        {
            var sb = new StringBuilder();

            if (Kind == "bank")
            {
                sb.Append($"moved {Moved} item{(Moved == 1 ? "" : "s")}");
            }
            else
            {
                sb.Append(Kind == "buy" ? "bought " : "sold ").Append(Detail);
                Detail = string.Empty;
            }

            int gold = GoldChange;

            if (gold != 0)
            {
                sb.Append(gold > 0 ? $", gold +{gold}" : $", gold {gold}");
            }

            if (Detail.Length != 0)
            {
                sb.Append("; ").Append(Detail.TrimEnd(' ', ';'));
            }

            return sb.ToString();
        }

        // ---------------------------------------------------------------- vendors

        private void Approach(uint now)
        {
            Mobile v = _world.Mobiles.Get(_vendor);

            if (v == null || v.IsDead)
            {
                Finish("failed", "the vendor is gone");

                return;
            }

            // ModernUO answers "vendor buy" only next to the vendor in AOS rules (4 tiles before).
            // Walk there with travel, which opens shop doors; follow the vendor if it wanders.
            if (v.Distance > 1)
            {
                (int gx, int gy) = _agent.TravelGoal;
                bool walking = _agent.TravelState == "walking";

                if (walking && Math.Max(Math.Abs(gx - v.X), Math.Abs(gy - v.Y)) <= 2)
                {
                    return;
                }

                if (!walking && _agent.TravelState != string.Empty && now - _askedAt < 3000)
                {
                    return;
                }

                if (_approaches >= MENU_AFTER && v.Distance <= MENU_RANGE)
                {
                    // Someone stands in the way: ask by the vendor's context menu, which reaches further.
                    State = "waiting";
                    _vendorName = Called(v);
                    _nearer = string.Empty;
                    _byMenu = true;
                    _askedAt = now;
                    _asks++;
                    _said = $"{(Kind == "buy" ? "Buy" : "Sell")} from {_vendorName}'s context menu";
                    NetClient.Socket.Send_RequestPopupMenu(v.Serial);

                    return;
                }

                if (_approaches >= 6)
                {
                    Finish("failed", "could not get next to the vendor");

                    return;
                }

                _askedAt = now;
                _approaches++;
                _agent.TravelForErrand(v.X, v.Y);

                return;
            }

            State = "waiting";
            _byMenu = false;
            UIManager.GetGump<ShopGump>()?.Dispose();
            _vendorName = Called(v);
            _nearer = HeardFirst(v);
            Ask(VendorWords(Kind == "buy", v.Name, _agent.LabelOf(v), _asks));
        }

        // A context menu from the server (packet 0xBF 0x14): the one this errand asked of its vendor has its Buy or Sell
        // chosen, and isn't shown. True when it was this errand's (PacketHandlers leaves it out of the screen).
        public bool OnPopup(PopupMenuData menu)
        {
            if (!_byMenu || State != "waiting" || menu == null || menu.Serial != _vendor)
            {
                return false;
            }

            _byMenu = false;
            int index = Choose(menu.Items, Kind == "buy");

            if (index < 0)
            {
                Finish("failed", $"{_vendorName}'s context menu has no {(Kind == "buy" ? "Buy" : "Sell")} to choose");
            }
            else
            {
                _askedAt = Time.Ticks; // the list comes at once, as after words
                NetClient.Socket.Send_PopupMenuSelection(menu.Serial, (ushort) index);
            }

            return true;
        }

        // The index of a context menu's Buy (or Sell) entry that can be chosen, or -1: a greyed one (flag 0x01) is out
        // of reach or not offered to this character.
        internal static int Choose(PopupMenuItem[] items, bool buy)
        {
            int want = buy ? CLILOC_BUY : CLILOC_SELL;

            foreach (PopupMenuItem it in items ?? Array.Empty<PopupMenuItem>())
            {
                if (it.Cliloc == want && (it.Flags & 0x01) == 0)
                {
                    return it.Index;
                }
            }

            return -1;
        }

        // What to say to a vendor for its list: "Minerva buy" the first NAMED_ASKS times, then
        // "vendor buy". The server gives "vendor buy" to the nearest vendor within a tile of the
        // speaker, whatever it sells, and to that one only (ModernUO sorts those in earshot by distance,
        // and the first vendor to hear marks the words handled). At the Britain healer that was the
        // healer guildmistress whenever she stood nearer than the healer: she shows no list, and a
        // soak run's first three buys failed with the healer a tile away (cuo-k0d). Only a vendor of
        // that name answers to it. Twice by name, as a vendor that steps away as it is asked doesn't
        // hear; "vendor buy" last, for a server that doesn't know its vendors by name.
        internal static string VendorWords(bool buy, string name, string label, int asked)
        {
            string who = asked < NAMED_ASKS ? CalledBy(name, label) : string.Empty;

            // The server reads *sell* before *buy*: "Giselle buy" would bring her sell list.
            if (who.Length == 0 || buy && who.Contains("sell", StringComparison.OrdinalIgnoreCase)
                || who.Contains("vendor", StringComparison.OrdinalIgnoreCase))
            {
                who = "vendor";
            }

            return $"{who} {(buy ? "buy" : "sell")}";
        }

        // The name a townsperson answers to: its own, or its label's, without the title
        // ("Minerva" of "Minerva the healer"), whose words could mean something else to the server.
        internal static string CalledBy(string name, string label)
        {
            string who = string.IsNullOrWhiteSpace(name) ? label ?? string.Empty : name;
            int title = who.IndexOf(" the ", StringComparison.OrdinalIgnoreCase);

            return (title > 0 ? who.Substring(0, title) : who).Trim();
        }

        // How soon one who stands (dx, dy, dz) from the speaker hears it, as ModernUO orders those in
        // earshot (LocationComparer): a tile straight on comes before a tile diagonally, and a
        // floor counts for little.
        internal static int HearingOrder(int dx, int dy, int dz) => 121 * (dx * dx + dy * dy) + dz * dz;

        // A townsperson as the log names it: with its title when the client knows it.
        private string Called(Mobile m) => _agent.LabelOf(m) ?? (string.IsNullOrWhiteSpace(m.Name) ? "someone" : m.Name.Trim());

        private int Hears(Mobile m)
        {
            int x = m.X, y = m.Y;

            if (m.Steps.Count != 0)
            {
                ref Mobile.Step step = ref m.Steps.Back();
                x = step.X;
                y = step.Y;
            }

            return HearingOrder(x - _world.RangeSize.X, y - _world.RangeSize.Y, m.Z - _world.Player.Z);
        }

        // The other townsfolk within a tile who hear the character no later than the vendor does: any
        // of them that is a vendor takes "vendor buy" before it. For the log, when a list doesn't come.
        private string HeardFirst(Mobile vendor)
        {
            int limit = Hears(vendor);
            var names = new List<string>();

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m != _world.Player && m != vendor && !m.IsDead && m.IsHuman && m.Distance <= 1
                    && m.NotorietyFlag == NotorietyFlag.Invulnerable && Hears(m) <= limit)
                {
                    names.Add(Called(m));
                }
            }

            return string.Join(", ", names);
        }

        private void WaitForList(uint now)
        {
            ShopGump gump = UIManager.GetGump<ShopGump>(_vendor);

            if (gump == null || gump.IsBuyGump != (Kind == "buy"))
            {
                if (now - _askedAt > LIST_TIMEOUT_MS)
                {
                    if (_asks < VENDOR_ASKS)
                    {
                        State = "approaching";
                    }
                    else
                    {
                        Finish("failed", Kind != "buy" ? "the vendor wants nothing you carry"
                            : _nearer.Length == 0 ? "the vendor did not show its goods"
                            : $"the vendor did not show its goods ({_nearer} stood as near and may have taken the words)");
                    }
                }

                return;
            }

            var picks = new List<Tuple<uint, ushort>>();
            var bought = new List<string>();
            List<(uint Serial, ushort Graphic, string Name, int Amount, uint Price)> offers = gump.Offers();

            // Why nothing could be bought, so the planner can act on it: sold out, too dear, not sold here.
            bool listed = false, soldOut = false, tooDear = false;

            if (Kind == "buy")
            {
                long gold = _world.Player.Gold;

                foreach ((string word, int amount) in Wanted(_items))
                {
                    foreach (var o in offers)
                    {
                        if (o.Price == 0 || !Matches(o.Name, o.Graphic, word))
                        {
                            continue;
                        }

                        listed = true;

                        if (o.Amount <= 0)
                        {
                            soldOut = true;

                            continue;
                        }

                        int n = (int) Math.Min(Math.Min(amount, o.Amount), gold / o.Price);
                        tooDear |= n <= 0;

                        if (n > 0)
                        {
                            picks.Add(Tuple.Create(o.Serial, (ushort) n));
                            bought.Add($"{n} {o.Name.Trim()}");
                            gold -= n * o.Price;
                        }

                        break;
                    }
                }
            }
            else
            {
                bool loot = string.Equals(_items.Trim(), "loot", StringComparison.OrdinalIgnoreCase);
                var words = new List<string>(Split(_items));

                foreach (var o in offers)
                {
                    Item it = _world.Items.Get(o.Serial);
                    bool pick = o.Serial != _agent.HeldWeapon
                                && (loot ? it != null && IsLoot(it) : words.Exists(w => Matches(o.Name, o.Graphic, w)));

                    if (pick && o.Amount > 0)
                    {
                        picks.Add(Tuple.Create(o.Serial, (ushort) o.Amount));
                        bought.Add($"{o.Amount} {o.Name.Trim()}");
                    }
                }
            }

            if (picks.Count == 0)
            {
                gump.Dispose();
                Finish("failed", Kind != "buy" ? "nothing to sell that the vendor wants"
                    : tooDear ? $"not enough gold (carrying {_world.Player.Gold})"
                    : soldOut ? "sold out here"
                    : listed ? "none of that on sale" : "this vendor doesn't sell that");

                return;
            }

            if (Kind == "buy")
            {
                NetClient.Socket.Send_BuyRequest(_vendor, picks.ToArray());
            }
            else
            {
                NetClient.Socket.Send_SellRequest(_vendor, picks.ToArray());
            }

            gump.Dispose();
            Moved = picks.Count;
            Detail = string.Join(", ", bought);
            State = "moving"; // nothing queued: finishes on the next step with the gold change
            _movedAt = now;
        }

        // The nearest townsperson who could be a vendor: human, not a player, not hostile.
        // A guildmaster stands in the shop with the vendors but sells nothing (ModernUO's
        // BaseGuildmaster is no active vendor): "vendor buy" next to one gets no answer.
        private bool IsGuildmaster(Mobile m)
        {
            string title = _agent.LabelOf(m) ?? m.Title ?? string.Empty;

            return title.Contains("guildmaster", StringComparison.OrdinalIgnoreCase) || title.Contains("guildmistress", StringComparison.OrdinalIgnoreCase);
        }

        private uint NearestVendor()
        {
            Mobile best = null;

            foreach (Mobile m in _world.Mobiles.Values)
            {
                if (m == _world.Player || m.IsDead || !m.IsHuman || m.Distance > 12 || AgentController.IsMonsterTarget(m)
                    || m.NotorietyFlag != NotorietyFlag.Invulnerable || IsGuildmaster(m))
                {
                    continue;
                }

                if (best == null || m.Distance < best.Distance)
                {
                    best = m;
                }
            }

            return best?.Serial ?? 0;
        }

        // ---------------------------------------------------------------- items

        private static int CountIn(Item container, ushort graphic)
        {
            int n = 0;

            for (LinkedObject o = container.Items; o != null; o = o.Next)
            {
                var it = (Item) o;

                if (it.Graphic == graphic)
                {
                    n += Math.Max((int) it.Amount, 1);
                }

                if (it.Items != null)
                {
                    n += CountIn(it, graphic);
                }
            }

            return n;
        }

        // Loot is what isn't kit: coins, supplies, spellbooks, runes and bags stay put.
        public static bool IsLoot(Item it)
        {
            ushort g = it.Graphic;

            if (it.IsCoin || it.ItemData.IsContainer || it.Items != null)
            {
                return false;
            }

            if (g == AgentController.BANDAGE_GRAPHIC || g >= 0x0F06 && g <= 0x0F0D || g == AgentSpells.SPELLBOOK_GRAPHIC
                || g == AgentSpells.NECRO_BOOK_GRAPHIC || g == AgentSpells.CHIVALRY_BOOK_GRAPHIC
                || g == 0x22C5 /* runebook */ || g >= 0x1F14 && g <= 0x1F17 /* recall runes */ || g >= 0x1F2D && g <= 0x1F72 /* scrolls */
                || AgentWeapons.IsAmmo(g) || AgentBard.IsInstrument(g))
            {
                return false;
            }

            foreach ((_, _, ushort reagent) in AgentSpells.ReagentGraphics)
            {
                if (g == reagent)
                {
                    return false;
                }
            }

            foreach ((_, _, ushort reagent) in AgentSpells.PaganReagentGraphics)
            {
                if (g == reagent)
                {
                    return false;
                }
            }

            return true;
        }

        private IEnumerable<Item> Find(Item container, string word)
        {
            for (LinkedObject o = container.Items; o != null; o = o.Next)
            {
                var it = (Item) o;

                if (Matches(AgentSnapshot.NameOf(_world, it), it.Graphic, word))
                {
                    yield return it;
                }

                if (it.Items != null)
                {
                    foreach (Item inner in Find(it, word))
                    {
                        yield return inner;
                    }
                }
            }
        }

        // An item word matches by name ("bandage" in "clean bandages") or by kind for the
        // common supplies, whose names vary by shard.
        private static bool Matches(string name, ushort graphic, string word)
        {
            word = word.Trim().ToLowerInvariant();

            switch (word)
            {
                case "bandage":
                case "bandages":
                    return graphic == AgentController.BANDAGE_GRAPHIC;
                case "heal potion":
                case "heal potions":
                    return graphic == AgentController.HEAL_POTION_GRAPHIC;
                case "cure potion":
                case "cure potions":
                    return graphic == AgentController.CURE_POTION_GRAPHIC;
                case "gold":
                    return graphic == 0x0EED;
            }

            foreach ((_, string reagent, ushort g) in AgentSpells.ReagentGraphics)
            {
                if (word == reagent.Replace('_', ' ') && graphic == g)
                {
                    return true;
                }
            }

            foreach ((_, string reagent, ushort g) in AgentSpells.PaganReagentGraphics)
            {
                if (word == reagent.Replace('_', ' ') && graphic == g)
                {
                    return true;
                }
            }

            return !string.IsNullOrEmpty(name) && name.ToLowerInvariant().Contains(word.TrimEnd('s'));
        }

        // "bandage:50, black pearl:20" -> (word, amount); a word with no amount means 1.
        private static IEnumerable<(string, int)> Wanted(string items)
        {
            foreach (string part in Split(items))
            {
                int colon = part.LastIndexOf(':');

                if (colon > 0 && int.TryParse(part.Substring(colon + 1), out int n))
                {
                    yield return (part.Substring(0, colon).Trim(), Math.Max(1, n));
                }
                else
                {
                    yield return (part, 1);
                }
            }
        }

        private static IEnumerable<string> Split(string s)
        {
            foreach (string part in (s ?? string.Empty).Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                yield return part.ToLowerInvariant();
            }
        }
    }
}
