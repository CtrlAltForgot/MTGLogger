import { useEffect, useMemo, useState } from "react";
import { Add, ArrowBack, Delete, Edit, Remove, Search } from "@mui/icons-material";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Grid,
  IconButton,
  InputAdornment,
  Stack,
  TablePagination,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { request } from "../api";
import { CardName } from "../components/CardDetails";
import type { AvailableCard, AvailablePage, Deck } from "../types";

export default function Decks() {
  const [decks, setDecks] = useState<Deck[]>([]),
    [selectedId, setSelectedId] = useState<string>(),
    [available, setAvailable] = useState<AvailableCard[]>([]),
    [chosen, setChosen] = useState<Set<string>>(new Set()),
    [query, setQuery] = useState(""),
    [open, setOpen] = useState(false),
    [editing, setEditing] = useState(false),
    [deleting, setDeleting] = useState(false),
    [error, setError] = useState<string>(),
    [busy, setBusy] = useState(false);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [availableTotal, setAvailableTotal] = useState(0),
    [availablePage, setAvailablePage] = useState(0),
    [availablePageSize, setAvailablePageSize] = useState(50);
  const [form, setForm] = useState({ name: "", format: "", description: "", image_url: "" });
  const selected = useMemo(
    () => decks.find((deck) => deck.id === selectedId),
    [decks, selectedId],
  );
  const loadDecks = () => request<Deck[]>("/decks").then(setDecks);
  const loadAvailable = async (
    id = selectedId,
    q = query,
    page = availablePage,
    pageSize = availablePageSize,
  ) => {
    if (id) {
      const result = await request<AvailablePage>(
        `/decks/${id}/available?q=${encodeURIComponent(q)}&page=${page + 1}&page_size=${pageSize}`,
      );
      setAvailable(result.items);
      setAvailableTotal(result.total);
      if (result.total > 0 && page * result.page_size >= result.total)
        setAvailablePage(
          Math.max(0, Math.ceil(result.total / result.page_size) - 1),
        );
    }
  };
  useEffect(() => {
    void loadDecks();
  }, []);
  useEffect(() => {
    setAvailablePage(0);
    setChosen(new Set());
    setQuantities({});
  }, [selectedId, query]);
  useEffect(() => {
    if (!selectedId) return;
    const timer = setTimeout(
      () =>
        void loadAvailable(
          selectedId,
          query,
          availablePage,
          availablePageSize,
        ).catch((e) =>
          setError(
            e instanceof Error ? e.message : "Could not load available cards",
          ),
        ),
      180,
    );
    return () => clearTimeout(timer);
  }, [selectedId, query, availablePage, availablePageSize]);
  const replaceDeck = (deck: Deck) => {
    setDecks((current) =>
      current.map((item) => (item.id === deck.id ? deck : item)),
    );
    setChosen(new Set());
    setQuantities({});
    void loadAvailable(deck.id);
  };
  const create = async () => {
    setBusy(true);
    try {
      const deck = await request<Deck>("/decks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          format: form.format || null,
          description: form.description || null,
          image_url: form.image_url || null,
        }),
      });
      setDecks((current) => [deck, ...current]);
      setSelectedId(deck.id);
      setOpen(false);
      setForm({ name: "", format: "", description: "", image_url: "" });
    } finally {
      setBusy(false);
    }
  };
  const openEditor = () => {
    if (!selected) return;
    setForm({name:selected.name,format:selected.format||"",description:selected.description||"",image_url:selected.image_url||""});
    setEditing(true);
  };
  const saveDeck = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      replaceDeck(await request<Deck>(`/decks/${selected.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:form.name,format:form.format||null,description:form.description||null,image_url:form.image_url||null})}));
      setEditing(false);
    } catch (e) { setError(e instanceof Error?e.message:"Could not update deck"); }
    finally { setBusy(false); }
  };
  const addSelected = async () => {
    if (!selected || !chosen.size) return;
    setBusy(true);
    setError(undefined);
    try {
      const entries = available
        .filter((card) => chosen.has(card.inventory.id))
        .map((card) => ({
          inventory_id: card.inventory.id,
          quantity: quantities[card.inventory.id] || 1,
        }));
      replaceDeck(
        await request<Deck>(`/decks/${selected.id}/entries`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add cards");
    } finally {
      setBusy(false);
    }
  };
  const setQuantity = async (entryId: string, quantity: number) => {
    if (!selected) return;
    setBusy(true);
    try {
      replaceDeck(
        await request<Deck>(
          `/decks/${selected.id}/entries/${entryId}?quantity=${Math.max(0, quantity)}`,
          { method: "PATCH" },
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update deck");
    } finally {
      setBusy(false);
    }
  };
  const removeDeck = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await request(`/decks/${selected.id}`, { method: "DELETE" });
      setDecks((current) => current.filter((deck) => deck.id !== selected.id));
      setSelectedId(undefined);
      setDeleting(false);
    } finally {
      setBusy(false);
    }
  };
  const allSelected =
    available.length > 0 &&
    available.every((card) => chosen.has(card.inventory.id));
  const toggleChoice = (id: string) => {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
        setQuantities((values) => {
          const copy = { ...values };
          delete copy[id];
          return copy;
        });
      } else {
        next.add(id);
        setQuantities((values) => ({ ...values, [id]: 1 }));
      }
      return next;
    });
  };
  const adjustChoice = (id: string, change: number, maximum: number) => {
    setChosen((current) => new Set(current).add(id));
    setQuantities((values) => ({
      ...values,
      [id]: Math.min(maximum, Math.max(1, (values[id] || 1) + change)),
    }));
  };
  const toggleAll = () => {
    if (allSelected) {
      setChosen(new Set());
      setQuantities({});
    } else {
      setChosen(new Set(available.map((card) => card.inventory.id)));
      setQuantities(
        Object.fromEntries(available.map((card) => [card.inventory.id, 1])),
      );
    }
  };

  if (!selected)
    return (
      <>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={2}
        >
          <Box>
            <Typography variant="h4">Decks</Typography>
            <Typography color="text.secondary">
              Build decks from physical copies in your collection.
            </Typography>
          </Box>
          <Button
            variant="contained"
            startIcon={<Add />}
            onClick={() => setOpen(true)}
          >
            New deck
          </Button>
        </Stack>
        <Grid container spacing={2} mt={1}>
          {decks.map((deck) => (
            <Grid size={{ xs: 12, sm: 6, lg: 4 }} key={deck.id}>
              <Card
                onClick={() => setSelectedId(deck.id)}
                sx={{
                  cursor: "pointer",
                  height: "100%",
                  position: "relative",
                  "&:hover": {
                    borderColor: "primary.main",
                    transform: "translateY(-2px)",
                  },
                  transition: "160ms ease",
                }}
                variant="outlined"
              >
                <DeckArtwork deck={deck}/>
                <CardContent>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="h6" fontWeight={800}>
                      {deck.name}
                    </Typography>
                    {deck.format && <Chip size="small" label={deck.format} />}
                  </Stack>
                  <Typography color="text.secondary" mt={1}>
                    {deck.total_cards} cards · {deck.unique_cards} unique
                  </Typography>
                  <Typography variant="h5" color="primary.main" mt={3}>
                    ${Number(deck.total_value).toFixed(2)}
                  </Typography>
                  {deck.description && (
                    <Typography variant="body2" color="text.secondary" mt={1}>
                      {deck.description}
                    </Typography>
                  )}
                  <Tooltip title="Edit deck"><IconButton aria-label={`Edit ${deck.name}`} onClick={event=>{event.stopPropagation();setSelectedId(deck.id);setForm({name:deck.name,format:deck.format||"",description:deck.description||"",image_url:deck.image_url||""});setEditing(true)}} sx={{position:"absolute",right:8,bottom:8}}><Edit/></IconButton></Tooltip>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
        {!decks.length && (
          <Typography textAlign="center" color="text.secondary" mt={10}>
            No decks yet. Create one and assign your scanned cards.
          </Typography>
        )}
        <CreateDialog
          open={open}
          form={form}
          setForm={setForm}
          busy={busy}
          close={() => setOpen(false)}
          create={create}
        />
      </>
    );

  return (
    <>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={3}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <IconButton onClick={() => setSelectedId(undefined)}>
            <ArrowBack />
          </IconButton>
          <Box>
            <Typography variant="h4">{selected.name}</Typography>
            <Typography color="text.secondary">
              {selected.total_cards} cards · {selected.unique_cards} unique · $
              {Number(selected.total_value).toFixed(2)}
            </Typography>
          </Box>
        </Stack>
        <Stack direction="row"><Button startIcon={<Edit/>} onClick={openEditor}>Edit deck</Button><Button color="error" startIcon={<Delete />} onClick={() => setDeleting(true)}>Delete deck</Button></Stack>
      </Stack>
      {error && (
        <Alert
          severity="error"
          onClose={() => setError(undefined)}
          sx={{ mb: 2 }}
        >
          {error}
        </Alert>
      )}
      <Grid container spacing={3}>
        <Grid size={{ xs: 12, lg: 5 }}>
          <Card>
            <CardContent>
              <Typography variant="h6">Deck list</Typography>
              <Divider sx={{ my: 2 }} />
              {selected.entries.map((entry) => (
                <Stack
                  key={entry.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  py={1}
                >
                  <Box
                    component="img"
                    src={entry.inventory.image_url || ""}
                    sx={{
                      width: 42,
                      height: 58,
                      objectFit: "cover",
                      objectPosition: "top",
                    }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Typography fontWeight={750} noWrap>
                      <CardName scryfallId={entry.inventory.scryfall_id}>
                        {entry.inventory.card_name}
                      </CardName>
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {entry.inventory.set_code.toUpperCase()} #
                      {entry.inventory.collector_number}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    disabled={busy}
                    onClick={() => setQuantity(entry.id, entry.quantity - 1)}
                  >
                    <Remove />
                  </IconButton>
                  <Typography fontWeight={800}>{entry.quantity}</Typography>
                  <IconButton
                    size="small"
                    disabled={busy}
                    onClick={() => setQuantity(entry.id, entry.quantity + 1)}
                  >
                    <Add />
                  </IconButton>
                  <Tooltip title="Remove from deck">
                    <IconButton
                      size="small"
                      color="error"
                      disabled={busy}
                      onClick={() => setQuantity(entry.id, 0)}
                    >
                      <Delete />
                    </IconButton>
                  </Tooltip>
                </Stack>
              ))}
              {!selected.entries.length && (
                <Typography color="text.secondary">
                  This deck is empty.
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, lg: 7 }}>
          <Card>
            <CardContent>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                gap={2}
              >
                <Box>
                  <Typography variant="h6">Deck Builder</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {availableTotal} unassigned entries match.
                  </Typography>
                </Box>
                <Button
                  variant="contained"
                  disabled={!chosen.size || busy}
                  onClick={addSelected}
                >
                  Add selected ({chosen.size})
                </Button>
              </Stack>
              <TextField
                fullWidth
                size="small"
                placeholder="Search available cards"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                sx={{ mt: 2 }}
                slotProps={{
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <Search />
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <Stack direction="row" alignItems="center" mt={1}>
                <Checkbox
                  checked={allSelected}
                  indeterminate={chosen.size > 0 && !allSelected}
                  onChange={toggleAll}
                />
                <Typography fontWeight={700}>
                  Select all on this page
                </Typography>
              </Stack>
              <Divider />
              {available.map((card) => (
                <Stack
                  key={card.inventory.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  py={1}
                >
                  <Checkbox
                    checked={chosen.has(card.inventory.id)}
                    onChange={() => toggleChoice(card.inventory.id)}
                  />
                  <Box
                    component="img"
                    src={card.inventory.image_url || ""}
                    sx={{
                      width: 42,
                      height: 58,
                      objectFit: "cover",
                      objectPosition: "top",
                    }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Typography fontWeight={750} noWrap>
                      <CardName scryfallId={card.inventory.scryfall_id}>
                        {card.inventory.card_name}
                      </CardName>
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {card.inventory.set_code.toUpperCase()} #
                      {card.inventory.collector_number}
                    </Typography>
                  </Box>
                  {card.available_quantity > 1 && (
                    <Stack
                      direction="row"
                      alignItems="center"
                      sx={{
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 2,
                      }}
                    >
                      <IconButton
                        size="small"
                        disabled={
                          !chosen.has(card.inventory.id) ||
                          (quantities[card.inventory.id] || 1) <= 1
                        }
                        onClick={() =>
                          adjustChoice(
                            card.inventory.id,
                            -1,
                            card.available_quantity,
                          )
                        }
                      >
                        <Remove fontSize="small" />
                      </IconButton>
                      <Typography minWidth={24} textAlign="center" fontWeight={900}>
                        {chosen.has(card.inventory.id)
                          ? quantities[card.inventory.id] || 1
                          : 1}
                      </Typography>
                      <IconButton
                        size="small"
                        disabled={
                          chosen.has(card.inventory.id) &&
                          (quantities[card.inventory.id] || 1) >=
                            card.available_quantity
                        }
                        onClick={() =>
                          adjustChoice(
                            card.inventory.id,
                            1,
                            card.available_quantity,
                          )
                        }
                      >
                        <Add fontSize="small" />
                      </IconButton>
                    </Stack>
                  )}
                </Stack>
              ))}
              {!available.length && (
                <Typography color="text.secondary" textAlign="center" py={5}>
                  No unassigned cards match this search.
                </Typography>
              )}
              {availableTotal > 0 && (
                <TablePagination
                  component="div"
                  count={availableTotal}
                  page={availablePage}
                  onPageChange={(_, page) => {
                    setAvailablePage(page);
                    setChosen(new Set());
                    setQuantities({});
                  }}
                  rowsPerPage={availablePageSize}
                  onRowsPerPageChange={(event) => {
                    setAvailablePageSize(Number(event.target.value));
                    setAvailablePage(0);
                    setChosen(new Set());
                    setQuantities({});
                  }}
                  rowsPerPageOptions={[25, 50, 100, 250]}
                  labelRowsPerPage="Cards per page"
                />
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      <Dialog open={deleting} onClose={() => setDeleting(false)}>
        <DialogTitle>Delete {selected.name}?</DialogTitle>
        <DialogContent>
          The cards remain in your collection and become available to other
          decks.
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(false)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            disabled={busy}
            onClick={removeDeck}
          >
            Delete deck
          </Button>
        </DialogActions>
      </Dialog>
      <DeckDialog open={editing} title="Edit deck" form={form} setForm={setForm} busy={busy} close={()=>setEditing(false)} submit={saveDeck} submitLabel="Save changes"/>
    </>
  );
}

type DeckForm={name:string;format:string;description:string;image_url:string}

function DeckArtwork({deck}:{deck:Deck}){
  const colors=[...new Set(deck.entries.flatMap(entry=>entry.inventory.color_identity.split('')).filter(Boolean))]
  const palette:Record<string,string>={W:'#eee4bf',U:'#4ea7d8',B:'#44384f',R:'#d85845',G:'#4f8a59'}
  const background=colors.length?`linear-gradient(135deg,${colors.map(color=>palette[color]||'#6c6265').join(',')})`:'linear-gradient(135deg,#322428,#130d0f)'
  return <Box sx={{height:120,background,position:'relative',overflow:'hidden'}}>{deck.image_url&&<Box component="img" src={deck.image_url} alt="" sx={{width:'100%',height:'100%',objectFit:'cover'}}/>}<Box sx={{position:'absolute',inset:0,background:'linear-gradient(0deg,rgba(10,6,7,.66),transparent 70%)'}}/></Box>
}

function CreateDialog({
  open,
  form,
  setForm,
  busy,
  close,
  create,
}: {
  open: boolean;
  form: DeckForm;
  setForm: (form: DeckForm) => void;
  busy: boolean;
  close: () => void;
  create: () => void;
}) {
  return <DeckDialog open={open} title="Create a deck" form={form} setForm={setForm} busy={busy} close={close} submit={create} submitLabel="Create deck"/>
}

function DeckDialog({open,title,form,setForm,busy,close,submit,submitLabel}:{open:boolean;title:string;form:DeckForm;setForm:(form:DeckForm)=>void;busy:boolean;close:()=>void;submit:()=>void;submitLabel:string}){
  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Custom image URL"
            placeholder="https://…"
            value={form.image_url}
            onChange={(e) => setForm({ ...form, image_url: e.target.value })}
            helperText="Leave blank to use a color-identity banner generated from the deck."
          />
          <TextField
            autoFocus
            label="Deck name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <TextField
            label="Format"
            placeholder="Commander, Modern, Casual…"
            value={form.format}
            onChange={(e) => setForm({ ...form, format: e.target.value })}
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!form.name.trim() || busy}
          onClick={submit}
        >
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
