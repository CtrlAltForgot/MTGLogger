import { useMemo, useState } from 'react'
import { AllInbox, CameraAlt, DarkMode, Dashboard as DashboardIcon, FactCheck, Inventory2, LightMode, Style } from '@mui/icons-material'
import { AppBar, Box, CssBaseline, IconButton, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography } from '@mui/material'
import Collection from './pages/Collection'
import Dashboard from './pages/Dashboard'
import Decks from './pages/Decks'
import ReviewQueue from './pages/ReviewQueue'
import Scanner from './pages/Scanner'
import Sealed from './pages/Sealed'
import { appTheme } from './theme'

const pages=[
  {name:'Dashboard',icon:<DashboardIcon/>,content:<Dashboard/>},
  {name:'Scanner',icon:<CameraAlt/>,content:<Scanner/>},
  {name:'Collection',icon:<Inventory2/>,content:<Collection/>},
  {name:'Decks',icon:<Style/>,content:<Decks/>},
  {name:'Review',icon:<FactCheck/>,content:<ReviewQueue/>},
  {name:'Sealed',icon:<AllInbox/>,content:<Sealed/>},
]

export default function App(){
  const requestedPage=new URLSearchParams(location.search).get('page')
  const requestedIndex=pages.findIndex(item=>item.name.toLowerCase()===requestedPage)
  const [page,setPage]=useState(requestedIndex>=0?requestedIndex:1)
  const [dark,setDark]=useState(()=>localStorage.getItem('mtglogger-theme')!=='light')
  const theme=useMemo(()=>appTheme(dark),[dark])
  const toggleTheme=()=>setDark(value=>{localStorage.setItem('mtglogger-theme',value?'light':'dark');return !value})
  const changePage=(value:number)=>{setPage(value);history.replaceState(null,'',`?page=${pages[value].name.toLowerCase()}`)}
  return <ThemeProvider theme={theme}><CssBaseline/>
    <AppBar position="sticky"><Toolbar sx={{minHeight:{xs:58,md:66},px:{xs:2,md:4}}}>
      <Box sx={{width:36,height:36,borderRadius:'11px',background:'linear-gradient(145deg,#78F0A8,#31BA6B)',color:'#062510',display:'grid',placeItems:'center',fontWeight:900,mr:1.5,boxShadow:'0 7px 20px rgba(52,211,115,.25)'}}>M</Box>
      <Box flex={1}><Typography variant="h6" lineHeight={1}>MTGLogger</Typography><Typography variant="caption" color="text.secondary">Collection, at card speed.</Typography></Box>
      <Tooltip title={dark?'Use light appearance':'Use dark appearance'}><IconButton onClick={toggleTheme} sx={{border:'1px solid',borderColor:'divider',bgcolor:'action.hover'}}>{dark?<LightMode/>:<DarkMode/>}</IconButton></Tooltip>
    </Toolbar>
    <Box sx={{px:{xs:1,md:4},display:'flex',justifyContent:{md:'center'}}}><Tabs value={page} onChange={(_,value)=>changePage(value)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>{pages.map(item=><Tab key={item.name} icon={item.icon} iconPosition="start" label={item.name}/>)}</Tabs></Box>
    </AppBar>
    <Box component="main" sx={{maxWidth:1500,mx:'auto',px:{xs:2,sm:3,lg:4},py:{xs:3,md:4},minHeight:'calc(100vh - 112px)'}}>{pages[page].content}</Box>
  </ThemeProvider>
}
