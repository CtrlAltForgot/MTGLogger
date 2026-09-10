import {artworkUrl} from '../artwork'
import { useEffect, useMemo, useState } from "react";
import { Add, ArrowBack, AutoAwesome, CloudUpload, Delete, Edit, Remove, Search } from "@mui/icons-material";
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
  MenuItem,
  Stack,
  TablePagination,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { API, request } from "../api";
import { CardName } from "../components/CardDetails";
import { recommendedDeckFormat } from "../decks/formatRecommendation";
import type { AutoDeckProposal, AvailableCard, AvailablePage, Deck, DeckFormatSuggestions } from "../types";

export default function Decks() {
  const [decks, setDecks] = useState<Deck[]>([]),
    [selectedId, setSelectedId] = useState<string>(),
    [available, setAvailable] = useState<AvailableCard[]>([]),
    [chosen, setChosen] = useState<Set<string>>(new Set()),
    [query, setQuery] = useState(""),
    [open, setOpen] = useState(false),
    [editing, setEditing] = useState(false),
    [deleting, setDeleting] = useState(false),
    [autoOpen,setAutoOpen]=useState(false),
    [error, setError] = useState<string>(),
    [busy, setBusy] = useState(false);
  const [coverFile,setCoverFile]=useState<File>();
  const [formatSuggestions,setFormatSuggestions]=useState<DeckFormatSuggestions>();
  const [detectingFormat,setDetectingFormat]=useState(false);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [availableTotal, setAvailableTotal] = useState(0),
    [availablePage, setAvailablePage] = useState(0),
    [availablePageSize, setAvailablePageSize] = useState(50);
  const [form, setForm] = useState({ name: "", format: "", description: "", image_url: "" });
  const [autoForm,setAutoForm]=useState<AutoBuildForm>({name:"My optimized deck",format:"Commander",colors:["U"],strategy:"balanced",focus:"",focus_mode:"prefer"});
  const [autoProposal,setAutoProposal]=useState<AutoDeckProposal>();
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
  const previewAutoDeck=async()=>{setBusy(true);setError(undefined);try{setAutoProposal(await request<AutoDeckProposal>('/decks/auto-build/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(autoForm)}))}catch(e){setError(e instanceof Error?e.message:'Could not build a deck proposal')}finally{setBusy(false)}};
  const applyAutoDeck=async()=>{setBusy(true);setError(undefined);try{const deck=await request<Deck>('/decks/auto-build/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(autoForm)});setDecks(current=>[deck,...current]);setSelectedId(deck.id);setAutoOpen(false);setAutoProposal(undefined)}catch(e){setError(e instanceof Error?e.message:'Could not create the proposed deck')}finally{setBusy(false)}};
  const openEditor = () => {
    if (!selected) return;
    setForm({name:selected.name,format:selected.format||"",description:selected.description||"",image_url:selected.image_url||""});
    setEditing(true);
    setCoverFile(undefined);
    setFormatSuggestions(undefined);
  };
  const saveDeck = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      let updated=await request<Deck>(`/decks/${selected.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:form.name,format:form.format||null,description:form.description||null,image_url:form.image_url||null})});
      if(coverFile){const body=new FormData();body.append('image',coverFile);updated=await request<Deck>(`/decks/${selected.id}/image`,{method:'POST',body})}
      replaceDeck(updated);
      setEditing(false);
    } catch (e) { setError(e instanceof Error?e.message:"Could not update deck"); }
    finally { setBusy(false); }
  };
  const detectFormat=async()=>{if(!selected)return;setDetectingFormat(true);setError(undefined);try{const result=await request<DeckFormatSuggestions>(`/decks/${selected.id}/format-suggestions`);setFormatSuggestions(result);const recommended=recommendedDeckFormat(result);if(recommended)setForm(current=>({...current,format:recommended}))}catch(e){setError(e instanceof Error?e.message:'Could not analyze deck format')}finally{setDetectingFormat(false)}};
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
          <Stack direction="row" spacing={1}><Button variant="outlined" startIcon={<AutoAwesome/>} onClick={()=>{setError(undefined);setAutoProposal(undefined);setAutoOpen(true)}}>Auto-build deck</Button><Button variant="contained" startIcon={<Add />} onClick={() => setOpen(true)}>New deck</Button></Stack>
        </Stack>
        {error&&<Alert severity="error" onClose={()=>setError(undefined)} sx={{mt:2}}>{error}</Alert>}
        <Grid container spacing={2} mt={1}>
          {decks.map((deck) => (
            <Grid size={{ xs: 12, sm: 6, lg: 4 }} key={deck.id}>
              <Card
                onClick={() => setSelectedId(deck.id)}
                sx={{
                  cursor: "pointer",
                  height: "100%",
                  maxWidth: 440,
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
                <CardContent sx={{pb:2,"&:last-child":{pb:2}}}>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="h6" fontWeight={800}>
                      {deck.name}
                    </Typography>
                    {deck.format && <Chip size="small" label={deck.format} />}
                  </Stack>
                  <Typography color="text.secondary" mt={0.5}>
                    {deck.total_cards} cards · {deck.unique_cards} unique
                  </Typography>
                  <Typography variant="h5" color="primary.main" mt={1.25}>
                    ${Number(deck.total_value).toFixed(2)}
                  </Typography>
                  {deck.description && (
                    <Typography variant="body2" color="text.secondary" mt={1}>
                      {deck.description}
                    </Typography>
                  )}
                  <Tooltip title="Edit deck"><IconButton aria-label={`Edit ${deck.name}`} onClick={event=>{event.stopPropagation();setSelectedId(deck.id);setForm({name:deck.name,format:deck.format||"",description:deck.description||"",image_url:deck.image_url||""});setCoverFile(undefined);setFormatSuggestions(undefined);setEditing(true)}} sx={{position:"absolute",right:8,bottom:6}}><Edit/></IconButton></Tooltip>
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
        <AutoBuildDialog open={autoOpen} form={autoForm} setForm={value=>{setAutoForm(value);setAutoProposal(undefined)}} proposal={autoProposal} error={error} busy={busy} close={()=>setAutoOpen(false)} preview={previewAutoDeck} apply={applyAutoDeck}/>
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
                    src={artworkUrl(entry.inventory.image_url)}
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
                    src={artworkUrl(card.inventory.image_url)}
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
      <DeckDialog open={editing} title="Edit deck" form={form} setForm={setForm} busy={busy} close={()=>setEditing(false)} submit={saveDeck} submitLabel="Save changes" coverFile={coverFile} setCoverFile={setCoverFile} suggestions={formatSuggestions} detectingFormat={detectingFormat} detectFormat={detectFormat}/>
    </>
  );
}

type DeckForm={name:string;format:string;description:string;image_url:string}
const deckFormats=['Standard','Pioneer','Modern','Pauper','Legacy','Vintage','Commander','Brawl','Oathbreaker','Duel Commander','Pauper Commander','Casual / Kitchen Table']

function DeckArtwork({deck}:{deck:Deck}){
  const pool=deck.entries.filter(entry=>entry.inventory.image_url)
  const seed=[...deck.id].reduce((value,character)=>value+character.charCodeAt(0),0)
  const spreadCount=Math.min(7,pool.length)
  const sampled=pool.length<=spreadCount?[...pool]:Array.from({length:spreadCount},(_,index)=>pool[(seed+index*997)%pool.length])
  const leaderPattern=/commander|brawl/i.test(deck.format||'')?/Legendary.*Creature|Creature.*Legendary/i:/oathbreaker/i.test(deck.format||'')?/Planeswalker/i:null
  const signature=leaderPattern&&pool.find(entry=>leaderPattern.test(entry.inventory.type_line||''))
  const cards=signature?[...sampled.filter(entry=>entry.id!==signature.id)]:sampled
  if(signature)cards.splice(Math.floor(cards.length/2),0,signature)
  if(cards.length>spreadCount)cards.pop()
  const custom=deck.image_url?.startsWith('/api/')?`${API}${deck.image_url}`:deck.image_url
  return <Box sx={{height:140,position:'relative',overflow:'hidden',background:'transparent'}}>{custom?<Box component="img" src={custom} alt="" sx={{width:'100%',height:'100%',objectFit:'cover'}}/>:<Box sx={{position:'absolute',inset:0,display:'flex',justifyContent:'center',alignItems:'center'}}>{cards.map((entry,index)=>{const midpoint=(cards.length-1)/2;const offset=index-midpoint;const isSignature=entry.id===signature?.id;return <Box key={entry.id} component="img" src={artworkUrl(entry.inventory.image_url!)} alt="" sx={{position:'absolute',width:isSignature?88:82,borderRadius:1,boxShadow:isSignature?'0 11px 28px rgba(0,0,0,.78)':'0 8px 22px rgba(0,0,0,.6)',transform:`translate(${offset*42}px, ${Math.abs(offset)*4-(isSignature?15:5)}px) rotate(${isSignature?0:offset*5.5}deg)`,transformOrigin:'50% 85%',zIndex:isSignature?20:index}}/>})}</Box>}{custom&&<Box sx={{position:'absolute',inset:0,background:'linear-gradient(0deg,rgba(10,6,7,.6),transparent 72%)'}}/>}</Box>
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

function DeckDialog({open,title,form,setForm,busy,close,submit,submitLabel,coverFile,setCoverFile,suggestions,detectingFormat,detectFormat}:{open:boolean;title:string;form:DeckForm;setForm:(form:DeckForm)=>void;busy:boolean;close:()=>void;submit:()=>void;submitLabel:string;coverFile?:File;setCoverFile?:(file?:File)=>void;suggestions?:DeckFormatSuggestions;detectingFormat?:boolean;detectFormat?:()=>void}){
  const acceptCover=(files:FileList|null)=>{const file=files?.[0];if(file&&setCoverFile)setCoverFile(file)}
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
          {setCoverFile&&<Box component="label" onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();acceptCover(event.dataTransfer.files)}} sx={{border:'1px dashed',borderColor:'divider',borderRadius:2,p:2,textAlign:'center',cursor:'pointer','&:hover':{borderColor:'primary.main',backgroundColor:'action.hover'}}}><CloudUpload color="primary"/><Typography fontWeight={800}>{coverFile?coverFile.name:'Drop a cover image here'}</Typography><Typography variant="caption" color="text.secondary">or click to choose a JPEG, PNG, or WebP</Typography><input hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={event=>acceptCover(event.target.files)}/></Box>}
          <TextField
            autoFocus
            label="Deck name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems="stretch"><TextField select sx={{flex:1}} label="Format" value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}><MenuItem value=""><em>Not selected</em></MenuItem>{deckFormats.map(format=><MenuItem key={format} value={format}>{format}</MenuItem>)}</TextField>{detectFormat&&<Button sx={{minWidth:190}} variant="outlined" startIcon={<AutoAwesome/>} disabled={detectingFormat} onClick={detectFormat}>{detectingFormat?'Analyzing…':'Identify format'}</Button>}</Stack>
          {suggestions&&<Box><Typography variant="body2" color="text.secondary" mb={1}>{suggestions.complete_deck?'These formats match both legality and deck structure.':`This ${suggestions.card_count}-card deck may be incomplete, so these are possibilities.`}</Typography><Stack direction="row" gap={1} flexWrap="wrap">{suggestions.suggestions.slice(0,6).map(item=><Tooltip key={item.format} title={item.reasons.join(' ')}><Chip clickable color={item.confidence==='high'?'success':'default'} label={`${item.format}${item.confidence==='possible'?' · possible':''}`} onClick={()=>setForm({...form,format:item.format})}/></Tooltip>)}</Stack>{!suggestions.suggestions.length&&<Typography color="warning.main">No supported current format fits every stored card.</Typography>}</Box>}
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

type AutoBuildForm={name:string;format:string;colors:string[];strategy:string;focus:string;focus_mode:'prefer'|'strict'}
const manaColors=[
  {key:'W',label:'White'}, {key:'U',label:'Blue'}, {key:'B',label:'Black'},
  {key:'R',label:'Red'}, {key:'G',label:'Green'}, {key:'C',label:'Colorless'},
]
const autoStrategies=[['balanced','Best available'],['aggro','Aggro'],['midrange','Midrange'],['control','Control'],['tokens','Tokens'],['artifacts','Artifacts'],['enchantments','Enchantments'],['graveyard','Graveyard'],['counters','Counters'],['lifegain','Lifegain'],['sacrifice','Sacrifice'],['spells','Spellslinger'],['creatures','Creature synergy']]

function AutoBuildDialog({open,form,setForm,proposal,error,busy,close,preview,apply}:{open:boolean;form:AutoBuildForm;setForm:(value:AutoBuildForm)=>void;proposal?:AutoDeckProposal;error?:string;busy:boolean;close:()=>void;preview:()=>void;apply:()=>void}){
  const toggleColor=(color:string)=>{
    if(color==='C'){setForm({...form,colors:['C']});return}
    const current=form.colors.filter(value=>value!=='C'),next=current.includes(color)?current.filter(value=>value!==color):[...current,color]
    setForm({...form,colors:next.length?next:['C']})
  }
  return (<Dialog open={open} onClose={close} fullWidth maxWidth="md">
    <DialogTitle><Stack direction="row" spacing={1} alignItems="center"><AutoAwesome color="primary"/><span>Auto-build a deck</span></Stack></DialogTitle>
    <DialogContent>
      <Typography color="text.secondary" mb={2}>Build from owned physical copies that are not assigned to any deck. Nothing moves until you approve the proposal.</Typography>
      {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}
      <Grid container spacing={2}>
        <Grid size={{xs:12,sm:7}}><TextField fullWidth label="Deck name" value={form.name} onChange={event=>setForm({...form,name:event.target.value})}/></Grid>
        <Grid size={{xs:12,sm:5}}><TextField select fullWidth label="Format" value={form.format} onChange={event=>setForm({...form,format:event.target.value})}>{deckFormats.map(format=><MenuItem key={format} value={format}>{format}</MenuItem>)}</TextField></Grid>
        <Grid size={{xs:12,sm:7}}><Typography variant="overline" color="text.secondary">Color identity</Typography><Stack direction="row" spacing={1} mt={.5}>{manaColors.map(mana=><Tooltip title={mana.label} key={mana.key}><IconButton aria-label={mana.label} aria-pressed={form.colors.includes(mana.key)} onClick={()=>toggleColor(mana.key)} sx={{width:46,height:46,p:.45,bgcolor:'rgba(255,255,255,.06)',border:'3px solid',borderColor:form.colors.includes(mana.key)?'primary.main':'transparent',boxShadow:form.colors.includes(mana.key)?'0 0 0 2px rgba(255,99,119,.28)':'none','&:hover':{bgcolor:'rgba(255,255,255,.12)',transform:'translateY(-1px)'}}}><Box component="img" src={`https://svgs.scryfall.io/card-symbols/${mana.key}.svg`} alt={`${mana.label} mana`} sx={{width:34,height:34,display:'block'}}/></IconButton></Tooltip>)}</Stack></Grid>
        <Grid size={{xs:12,sm:5}}><TextField select fullWidth label="Strategy" value={form.strategy} onChange={event=>setForm({...form,strategy:event.target.value})}>{autoStrategies.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField></Grid>
        <Grid size={{xs:12,sm:7}}><TextField fullWidth label="Set or collection focus (optional)" placeholder="Avatar" value={form.focus} onChange={event=>setForm({...form,focus:event.target.value})} helperText="Matches the set name or code across your unassigned collection."/></Grid>
        <Grid size={{xs:12,sm:5}}><TextField select fullWidth label="Collection focus mode" value={form.focus_mode} disabled={!form.focus.trim()} onChange={event=>setForm({...form,focus_mode:event.target.value as 'prefer'|'strict'})} helperText={form.focus_mode==='strict'?'Every selected card must match.':'Prefer matches, then use the best supporting cards.'}><MenuItem value="prefer">Prefer matching cards</MenuItem><MenuItem value="strict">Only matching cards</MenuItem></TextField></Grid>
      </Grid>
      {proposal&&<Box mt={3}>
        <Alert severity={proposal.complete?'success':'warning'}><Typography fontWeight={900}>{proposal.complete?`Complete ${proposal.total_cards}-card deck ready`:`${proposal.total_cards} of ${proposal.target_size} cards available`}</Typography><Typography variant="body2">{proposal.explanation.join(' ')}</Typography></Alert>
        {proposal.warnings.map(warning=><Alert severity="warning" variant="outlined" sx={{mt:1}} key={warning}>{warning}</Alert>)}
        <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap my={2}><Chip color="primary" label={`Theme · ${proposal.theme.replaceAll('-',' ')}`}/><Chip color="success" label={`Quality ${proposal.quality_score.toFixed(0)}/100`}/><Chip color="info" label={`Synergy ${proposal.synergy_score.toFixed(0)}/100`}/><Chip label={`${proposal.land_count} lands`}/><Chip label={`Average MV ${proposal.average_mana_value.toFixed(2)}`}/>{proposal.matched_sets.map(set=><Chip size="small" variant="outlined" color="secondary" key={set} label={set}/>)}{Object.entries(proposal.role_counts).filter(([role])=>!['land','leader'].includes(role)).map(([role,count])=><Chip size="small" variant="outlined" key={role} label={`${count} ${role}`}/>)}</Stack>
        <Card variant="outlined" sx={{maxHeight:390,overflowY:'auto'}}>
          <CardContent>
            {proposal.cards.map(card=><Stack key={card.inventory_id} direction="row" spacing={1.25} alignItems="center" py={.75}>
              <Box component="img" src={artworkUrl(card.image_url||'')} alt="" sx={{width:38,height:53,objectFit:'cover',objectPosition:'top',borderRadius:.75,flexShrink:0}}/>
              <Typography fontWeight={900} minWidth={28}>×{card.quantity}</Typography>
              <Box flex={1} minWidth={0}>
                <Typography fontWeight={800} noWrap><CardName scryfallId={card.scryfall_id}>{card.name}</CardName></Typography>
                <Typography variant="caption" color="text.secondary">{card.set_name} · {card.set_code.toUpperCase()} #{card.collector_number} · MV {card.mana_value.toFixed(1)} · {card.reasons.join(' · ')}</Typography>
              </Box>
              <Chip size="small" color={card.role==='leader'?'primary':card.role==='land'?'success':'default'} label={card.role}/>
            </Stack>)}
          </CardContent>
        </Card>
      </Box>}
    </DialogContent>
    <DialogActions><Button onClick={close}>Cancel</Button>{proposal&&<Button disabled={busy||!proposal.cards.length} variant="contained" color={proposal.complete?'primary':'warning'} onClick={apply}>Create this deck</Button>}<Button disabled={busy||!form.name.trim()||!form.colors.length} variant={proposal?'outlined':'contained'} startIcon={<AutoAwesome/>} onClick={preview}>{busy?'Optimizing…':proposal?'Build again':'Build proposal'}</Button></DialogActions>
  </Dialog>)
}
