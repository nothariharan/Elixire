"""One-shot test: create room, add receptionist, verify it works."""
import asyncio, sys, os
sys.path.insert(0, 'C:/Users/HARIHARAN/Desktop/Elixir/elixir')
os.chdir('C:/Users/HARIHARAN/Desktop/Elixir/elixir')
import env_loader  # noqa

async def test():
    from gateway.config import load_agent_credentials
    from gateway.client import BandClient, format_case_message, case_payload

    gateway = load_agent_credentials("gateway")
    recep   = load_agent_credentials("receptionist")
    intake  = load_agent_credentials("intake")
    brief   = load_agent_credentials("brief")

    print("gateway:", gateway.agent_id)
    print("recep:  ", recep.agent_id)

    async with BandClient(gateway) as client:
        room_id = await client.create_chat()
        print("room_id:", room_id)

        for creds in (recep, intake, brief):
            try:
                await client.add_participant(room_id, creds.agent_id)
                print(f"add_participant({creds.name}) -> OK")
            except Exception as e:
                print(f"add_participant({creds.name}) FAILED: {e}")

        payload = case_payload(
            raw_input='{"patient_name":"Test","chief_complaint":"headache","clinic_id":"general_practice","locale":"en"}',
            mode="band", locale="en",
            patient_history=[], patient_responses=[], follow_up_count=0
        )
        msg = format_case_message(recep, payload)
        try:
            await client.send_message(room_id, msg, recep)
            print("send_message -> OK")
        except Exception as e:
            print(f"send_message FAILED: {e}")

    print("\nDone. Room:", room_id)
    print("Watch receptionist.log for activity on this room_id.")

asyncio.run(test())
